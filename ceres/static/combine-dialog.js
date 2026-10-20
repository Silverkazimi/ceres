import { COMMIT_ICON } from './button-icons.js';
import { foilBadgeHtml } from './badges.js';
import { BOARD, CURRENT_PLAYER, catalogNotice, noticeMessage, playerQueryAppend } from './board.js';
import { IDLE_COMBINE_GROUPS, applyCombineConfirmedUpdate, combineApplyQuantity, combineApplySelection, combineAvailableCopies, combineCopyRowsHtml, combineDefaultQuantity, combineLedgerData, combineObservedAt, combineGroupKey, combinePresetQuantities, combineReason, combineToMaxCopies, idleCombineGroups, prefetchIdleCardsCache, settleCombineRefresh } from './combine.js';
import { editorCardImage, editorCardImageHtml, editorGroups, editorOccupant, editorSlotLabel } from './editor.js';
import { esc, formatPlotLabel, num } from './format.js';
import { OPERATION_RECEIPTS, beginSharedOperation, clearOperationPollTimer, publishOperationReceipt, scheduleOperationPoll, stopOperationPoll } from './operations.js';
import { handleSlotEditorClick } from './picker-actions.js';
import { updateMountedRow } from './rows.js';
import { CERES_STAGE, mutateEditorSlot, stageForSlot, stageToast } from './staging.js';
import { S } from './state.js';
import { scrollPlotAfterSlotAdd } from './table.js';

export const COMBINE_SELECTION = new Map(); // uid -> card, the ticked set sent to /api/combine/preview and /build
let COMBINE_PREVIEW = null;
let COMBINE_VERIFY = null;
let COMBINE_VERIFY_RUN = 0;
let COMBINE_PREVIEW_RUN = 0;
// Gather-all combine dialog (v0.7.28 -- replaces the old per-row inline picker
// rejected on use: finding N copies of a card by hand in a table of ~7,700 idle
// cards was the actual defect, not the preview/sign flow underneath it, which is
// unchanged). Holds every owned copy of one (card, edition, foil) group and which
// of them the server has refused so far; null while the dialog is closed.
export let COMBINE_DIALOG = null;
let COMBINE_DIALOG_RETURN_FOCUS = null;
// Set after the broadcast and retained through domain verification as the
// "Arrive" state (maintainer report 2026-08-30: a real combine gave zero feedback).
// The same overlay stays open through provisional, confirmed, and close states.
// {name, ledger, survivorUid, survivorCopy, destroyedCount, tileContext}.
let COMBINE_ARRIVAL = null;
// Set only after a tile-triggered (door 2) combine reaches domain-confirmed
// {deedUid, groupId, slotIndex, survivorUid, survivorCopy, combineVerifiedAt}.
// Owner's ruling: ASK, never act automatically -- exactly two choices, no default.
let COMBINE_STAKE_PROMPT = null;
// v0.7.84 -- the legible wait between pressing Combine and a verdict (states 05/06
// work/combine-modal-redesign): {step: 'keychain'|'chain', txId}. Set only while
// waiting on Keychain / the tx id it returns; cleared the moment verifyCombineBroadcast()
// takes over (its own COMBINE_VERIFY.loading covers the third step, 'Confirming in
// game') so exactly one of the two ever drives the progress panel at a time.
let COMBINE_SIGN_STEP = null;
// set once by openCombineInPlace() on the Cards page, which has no board: a confirmed combine calls the page's
// onConfirmed (it refreshes Find combinables and My cards) instead of settleCombineRefresh(), which would load the board
// closing tells the page whether a sent combine was still unconfirmed (onClosed). Never set on the board, never cleared.
let COMBINE_HOST = null;
// board.js's NOTICE_CATALOG comes with the board read (/api/board), which the Cards page never makes; there the
// dialog's own notices fall back to this copy, verbatim from ceres/notices.py (test_ir9279_combine_in_place.py compares it
// with notice_catalog()). On the board the served catalog is used as before.
const COMBINE_NOTICE_FALLBACK = {
  combine_select: 'Select this card for an explicit combine preview.',
  combine_preview_loading: 'Checking the selected cards before you combine them…',
  combine_refusal: 'Combine refused by the game: {reason}. This decision used the state observed at {observed_at}.',
  combine_sign: 'Combine',
  combine_confirmed: 'Combine confirmed in game; the card surface is updating.',
  combine_key_tier_refused: 'Hive Keychain signed with {key}, not Posting. This must be Posting-tier — reject the {key} prompt in Keychain and try again.',
};
function combineNoticeText(code, context = {}) {
  const notice = catalogNotice(code, context);
  if (!notice.message && COMBINE_NOTICE_FALLBACK[code]) notice.message = COMBINE_NOTICE_FALLBACK[code];
  return noticeMessage(notice);
}
// Only reached when a plot has MORE THAN ONE qualifying worker (plotCombineCandidates
// above) -- the single-candidate case opens the real dialog directly. Reuses the same
// overlay shell (ensureCombineDialogOverlay) and the existing 'open-group' combine
// action (handleCombineAction) rather than inventing a second dialog -- this is a
// picker step in front of the one real dialog, never a second modal.
export function openPlotCombineChooser(row, candidates) {
  ensureCombineDialogOverlay();
  const overlay = document.getElementById('combine-dialog-overlay');
  const title = document.getElementById('combine-dialog-title');
  const body = document.getElementById('combine-dialog-body');
  COMBINE_DIALOG_RETURN_FOCUS = document.activeElement;
  COMBINE_DIALOG = null;
  COMBINE_PREVIEW = null;
  COMBINE_VERIFY = null;
  COMBINE_SIGN_STEP = null;
  candidates.forEach((c) => IDLE_COMBINE_GROUPS.set(c.key, c.copies));
  title.textContent = `Combine — Plot ${formatPlotLabel(row.plot)}`;
  const rows = candidates.map((c) => `<li><button type="button" class="picker-chip ceres-btn ceres-btn--secondary" data-control-density="compact" data-combine-action="open-group" data-combine-group="${esc(c.key)}">${esc(c.name)}${foilBadgeHtml(c.copies[0], 'combine-chooser-foil')} — ${c.copies.length} idle ${c.copies.length === 1 ? 'copy' : 'copies'}</button></li>`).join('');
  body.innerHTML = `<p>This plot has more than one worker with combinable idle duplicates. Choose one:</p><ul class="combine-plot-chooser">${rows}</ul>`;
  overlay.hidden = false;
  overlay.querySelector('.hazard-close')?.focus();
}

// State 05/06 step order -- named, not implicit (work/combine-modal-redesign)
// 'keychain' waits on the extension, 'chain' has a tx id, 'verify' is checking
// game state. combineStepState() below reads a step's position against the
// current phase to render it waiting/active/done.
const COMBINE_PROGRESS_STEPS = [
  { key: 'keychain', label: 'Approve the transaction in the extension window' },
  { key: 'chain', label: 'On chain' },
  { key: 'verify', label: 'Confirming in game' },
];

function combineStepState(stepKey, phase) {
  const order = COMBINE_PROGRESS_STEPS.map((s) => s.key);
  const stepIdx = order.indexOf(stepKey);
  const phaseIdx = order.indexOf(phase);
  if (stepIdx < phaseIdx) return 'done';
  if (stepIdx === phaseIdx) return 'active';
  return 'waiting';
}

// The wait between pressing Combine and a verdict, as three NAMED steps
// (work/combine-modal-redesign states 05/06) -- replaces a dialog that used to
// show nothing distinguishable while waiting. Each step moves
// waiting -> active -> done; the hero card above this (renderCombineDialogEditor)
// is untouched, so the player keeps seeing what is being combined throughout.
function combineProgressStepsHtml(phase, { txId, attempt, nextPollAt } = {}) {
  return `<div class="combine-progress" role="status">${COMBINE_PROGRESS_STEPS.map((s) => {
    const state = combineStepState(s.key, phase);
    const label = s.key === 'verify' && state === 'active' && attempt ? `${s.label} · check ${attempt}` : s.label;
    const detail = s.key === 'chain' && txId && state !== 'waiting' ? `<code class="combine-step-detail">${esc(txId)}</code>` : '';
    const nextCheck = s.key === 'verify' && state === 'active' && nextPollAt
      ? `<span class="combine-step-detail">Next check in about ${Math.max(1, Math.ceil((nextPollAt - Date.now()) / 1000))} seconds</span>` : '';
    return `<div class="combine-step is-${state}" data-combine-step="${esc(s.key)}"><span class="combine-step-dot" aria-hidden="true"></span><span class="combine-step-label">${esc(label)}</span>${detail}${nextCheck}</div>`;
  }).join('')}</div>`;
}

// State 06b -- three distinct non-success surfaces (the internal rules's binding
// four-verdict split): none may look like the others, and none may look like
// success. domain-confirmed never reaches here -- verifyCombineBroadcast()
// dispatches to the Arrive state (renderCombineArrival) before this can render.
function combineVerdictHtml() {
  const result = COMBINE_VERIFY?.result;
  const attemptText = COMBINE_VERIFY?.attempt
    ? `<p class="combine-verdict-attempt">Checked the game ${esc(COMBINE_VERIFY.attempt)}×.</p>`
    : '';
  if (COMBINE_VERIFY?.error) {
    return `<div class="combine-verdict is-could-not-verify" role="alert">` +
      `<p class="combine-verdict-title">Could not check game state</p>` +
      `<p class="combine-verdict-detail">Your transaction is on chain. ${esc(COMBINE_VERIFY.error)}</p>` +
      attemptText + combineRetryButtonHtml(result) + `</div>`;
  }
  if (!result) return '';
  if (result.state === 'game-refused') {
    const notice = result.notices?.find((item) => item?.code === 'verification_game_refused');
    const reason = notice?.reason || result.game?.evidence?.reason || result.game?.evidence?.error || 'The game refused this operation.';
    return `<div class="combine-verdict is-game-refused" role="alert">` +
      `<p class="combine-verdict-title">The game refused this operation</p>` +
      `<p class="combine-verdict-detail">${esc(reason)}</p>` +
      attemptText + `</div>`;
  }
  // The ONLY failure verdict here -- leads with the fact the player needs
  // first: nothing was destroyed.
  if (result.state === 'domain-mismatch') {
    return `<div class="combine-verdict is-mismatch" role="alert">` +
      `<p class="combine-verdict-title">The game did not accept this</p>` +
      `<p class="combine-verdict-detail">Nothing was combined. No cards were destroyed.</p>` +
      attemptText + combineRetryButtonHtml(result) + `</div>`;
  }
  if (result.state === 'domain-could-not-verify') {
    return `<div class="combine-verdict is-could-not-verify" role="alert">` +
      `<p class="combine-verdict-title">Could not check game state</p>` +
      `<p class="combine-verdict-detail">Your transaction is on chain.</p>` +
      attemptText + combineVerificationNextPollHtml() + combineRetryButtonHtml(result) + `</div>`;
  }
  // Pending and unrecognised states remain unresolved, never success.
  const escalated = !!(result.domain?.evidence?.pending_metadata?.requires_escalation);
  return `<div class="combine-verdict ${escalated ? 'is-pending-escalated' : 'is-pending'}" role="status">` +
    `<p class="combine-verdict-title">${escalated ? 'Still not confirmed' : 'Not confirmed yet'}</p>` +
    `<p class="combine-verdict-detail">${escalated
      ? 'This did not resolve in the expected window. It may still be propagating, or it may need attention — check the game directly, or try again.'
      : 'The game may still be catching up.'}</p>` +
    attemptText + combineVerificationNextPollHtml() + combineRetryButtonHtml(result) + `</div>`;
}

function combineVerificationNextPollHtml() {
  const arrival = typeof COMBINE_ARRIVAL === 'undefined' ? null : COMBINE_ARRIVAL;
  const receipts = typeof OPERATION_RECEIPTS === 'undefined' ? null : OPERATION_RECEIPTS;
  const entryId = arrival?.operationReceipt?.entryId;
  const receipt = entryId && receipts ? receipts.get(entryId) : null;
  const nextAt = receipt?.nextPollAt;
  if (!nextAt) return '';
  const seconds = Math.max(1, Math.ceil((nextAt - Date.now()) / 1000));
  return `<p class="combine-verdict-next">Checking again in ${seconds}s.</p>`;
}

// The retry action's OWN label ("Check again", notices.py's
// RETRY_VERIFICATION_ACTION) -- not the pending notice's own message, which is
// what this used to render ("Not confirmed yet…" as a button label, a known
// copy defect: even the escape hatch didn't read like one).
function combineRetryButtonHtml(result) {
  const label = result?.notices?.find((item) => item?.action?.id === 'retry_verification')?.action?.label || 'Check again';
  return `<button type="button" class="picker-chip ceres-btn ceres-btn--secondary" data-control-density="compact" data-combine-action="verify">${esc(label)}</button>`;
}

function combineVerificationHtml() {
  if (COMBINE_SIGN_STEP) return combineProgressStepsHtml(COMBINE_SIGN_STEP.step, { txId: COMBINE_SIGN_STEP.txId });
  if (!COMBINE_VERIFY) return '';
  const arrival = typeof COMBINE_ARRIVAL === 'undefined' ? null : COMBINE_ARRIVAL;
  const receipts = typeof OPERATION_RECEIPTS === 'undefined' ? null : OPERATION_RECEIPTS;
  const entryId = arrival?.operationReceipt?.entryId;
  const receipt = entryId && receipts ? receipts.get(entryId) : null;
  if (COMBINE_VERIFY.loading) return combineProgressStepsHtml('verify', { txId: COMBINE_VERIFY.txId, attempt: COMBINE_VERIFY.attempt, nextPollAt: receipt?.nextPollAt });
  return combineVerdictHtml();
}

// Creates the gather-all combine dialog overlay once (same lazy-singleton
// pattern as ensureHazardOverlay()). A LOWER z-index than .slot-hazard-overlay
// (100) so the irreversible-combine hazard confirmation always stacks visibly
// on top when 'Combine and sign' opens it, never behind this dialog.
function ensureCombineDialogOverlay() {
  if (document.getElementById('combine-dialog-overlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'combine-dialog-overlay';
  overlay.className = 'slot-hazard-overlay combine-dialog-overlay';
  overlay.hidden = true;
  overlay.innerHTML = '<div class="slot-hazard-panel combine-dialog-panel" role="dialog" aria-modal="true" aria-labelledby="combine-dialog-title" aria-describedby="combine-dialog-body"><header><h3 id="combine-dialog-title">Combine</h3><button type="button" class="hazard-close ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-combine-action="close-dialog" aria-label="Close">×</button></header><div class="slot-hazard-body combine-dialog-body" id="combine-dialog-body"></div></div>';
  // Backdrop click closes instantly (maintainer, 2026-08-30) -- bound before the
  // delegated handler below, and only fires on the overlay itself (the
  // backdrop), never from inside `.combine-dialog-panel`.
  overlay.addEventListener('click', (event) => { if (event.target === overlay) closeCombineDialog(); });
  overlay.addEventListener('click', handleSlotEditorClick);
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeCombineDialog(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...overlay.querySelectorAll('button:not([disabled]), input:not([disabled])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.appendChild(overlay);
}

// Opens the gather-all dialog for every owned copy of one (card, edition, foil)
// group. Starts ticked from the group's `available` copies (the idle-bucket-level
// checks already computed server-side: not listed/rented/delegated, not
// lock-held) then refines against the server's OWN combine rules via
// requestCombinePreview()'s retry loop below -- this dialog never reimplements
// fides_cards/ops.py's block list client-side, so a rule change there can't
// silently drift out of sync with what gets pre-ticked.
// `preselect` is the Card ID list of the set Find combinables proposed (read by the board itself from
// api/cards/combinables, never carried in the address); it is taken only when every copy in it is still available here.
export function openCombineGroupDialog(key, { preselect = null } = {}) {
  document.getElementById('combine-dialog-overlay')?.classList.remove('is-plan-mode');
  const copies = IDLE_COMBINE_GROUPS.get(key);
  if (!copies || copies.length < 2) return;
  ensureCombineDialogOverlay();
  const overlay = document.getElementById('combine-dialog-overlay');
  COMBINE_DIALOG_RETURN_FOCUS = document.activeElement;
  const sorted = [...copies].sort((a, b) => (b.bcx ?? -Infinity) - (a.bcx ?? -Infinity) || String(a.uid).localeCompare(String(b.uid)));
  const blocked = new Map();
  sorted.forEach((c) => { if (!c.uid) blocked.set(String(c.asset_ref), 'this copy cannot be signed for'); else if (!c.available) blocked.set(String(c.uid), c.blocked_reason || 'not available'); });
  COMBINE_SELECTION.clear();
  sorted.forEach((c) => { if (c.uid && c.available) COMBINE_SELECTION.set(String(c.uid), c); });
  COMBINE_DIALOG = { key, copies: sorted, blocked, name: sorted[0]?.name || 'card' };
  COMBINE_PREVIEW = null;
  COMBINE_VERIFY = null;
  COMBINE_ARRIVAL = null;
  COMBINE_SIGN_STEP = null;
  // Refines the pretick above from "every eligible copy" down to the actual
  // default the redesigned dialog opens on -- "To max level" when a BCX ceiling
  // is known for this printing, else every copy stays selected (there's no
  // ceiling to aim short of). combineApplyQuantity is the single place that
  // turns a quantity into explicit Card IDs, so this never diverges from what
  // the amount control itself would pick for the same number.
  // "To max level" is the fit-to-max selection (never past max CC); : a proposed set wins when it is valid.
  const proposed = Array.isArray(preselect) && preselect.length >= 2 ? preselect.map(String) : null;
  if (proposed && combineApplySelection(COMBINE_DIALOG, proposed, 'proposed')) COMBINE_DIALOG.proposed = proposed;
  else {
    const fit = combineToMaxCopies(COMBINE_DIALOG);
    if (!(fit && combineApplySelection(COMBINE_DIALOG, fit, 'to_max'))) combineApplyQuantity(COMBINE_DIALOG, combineDefaultQuantity(COMBINE_DIALOG));
  }
  overlay.hidden = false;
  renderCombineDialog();
  overlay.querySelector('.hazard-close')?.focus();
  if (COMBINE_SELECTION.size >= 2) requestCombinePreview();
}

export function closeCombineDialog() {
  // a combine is unresolved while the wallet is open, after an ambiguous broadcast, or once sent without the
  // game's verdict -- the page then keeps its Combine closed so it is never signed twice.
  const outcome = COMBINE_ARRIVAL && !COMBINE_ARRIVAL.provisional ? 'confirmed'
    : COMBINE_SIGN_STEP || COMBINE_ARRIVAL?.provisional || COMBINE_PREVIEW?.broadcastStalled ? 'unknown' : 'none';
  const wasOpen = !!COMBINE_DIALOG && COMBINE_DIALOG.mode !== 'plan';
  const overlay = document.getElementById('combine-dialog-overlay');
  if (overlay) { overlay.hidden = true; overlay.classList.remove('is-plan-mode'); }
  const entryId = COMBINE_ARRIVAL?.operationReceipt?.entryId;
  if (entryId) {
    stopOperationPoll(entryId);
    OPERATION_RECEIPTS.delete(entryId);
  }
  COMBINE_VERIFY_RUN += 1;
  COMBINE_DIALOG = null;
  COMBINE_SELECTION.clear();
  COMBINE_PREVIEW = null;
  COMBINE_VERIFY = null;
  COMBINE_ARRIVAL = null;
  COMBINE_SIGN_STEP = null;
  if (COMBINE_DIALOG_RETURN_FOCUS && typeof COMBINE_DIALOG_RETURN_FOCUS.focus === 'function') COMBINE_DIALOG_RETURN_FOCUS.focus();
  COMBINE_DIALOG_RETURN_FOCUS = null;
  if (wasOpen) COMBINE_HOST?.onClosed?.({ outcome });
}

// Post-combine stake-choice prompt (door 2 only, see COMBINE_STAKE_PROMPT's own
// comment) -- a SEPARATE small overlay stacked on top of the combine dialog
// (higher z-index, see ensureCombineDialogOverlay's own comment), not an extra
// panel appended to its body: the combine dialog itself now stays open showing
// the "Arrive" state (renderCombineArrival) behind this, so the player answers
// the stake-or-not choice first and lands on the full confirmation afterward.
// Same overlay shell/CSS as the sole-power-source hazard confirm (ensureHazardOverlay)
// match the existing control vocabulary rather than inventing new markup.
function ensureCombineStakePromptOverlay() {
  if (document.getElementById('combine-stake-prompt-overlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'combine-stake-prompt-overlay';
  overlay.className = 'slot-hazard-overlay';
  overlay.hidden = true;
  overlay.innerHTML = '<div class="slot-hazard-panel" role="dialog" aria-modal="true" aria-labelledby="combine-stake-prompt-title" aria-describedby="combine-stake-prompt-body"><header><h3 id="combine-stake-prompt-title">Combine confirmed</h3><button type="button" class="hazard-close ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-combine-stake-action="skip" aria-label="Close">×</button></header><div class="slot-hazard-body" id="combine-stake-prompt-body"></div><footer><button type="button" class="slot-bulk ceres-btn ceres-btn--secondary" data-control-density="compact" data-combine-stake-action="skip">Keep it off the plot</button><button type="button" class="slot-review ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-combine-stake-action="stake">Stake it into this slot</button></footer></div>';
  // Backdrop click closes instantly (maintainer, 2026-08-30) -- "skip" is this
  // prompt's own close action (declining to stake, same as its header ×), so
  // the backdrop routes to the same handler rather than inventing a new exit.
  overlay.addEventListener('click', (event) => { if (event.target === overlay) closeCombineStakePrompt(); });
  overlay.addEventListener('click', handleSlotEditorClick);
  overlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeCombineStakePrompt(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...overlay.querySelectorAll('button:not([disabled])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.appendChild(overlay);
}

function closeCombineStakePrompt() {
  const overlay = document.getElementById('combine-stake-prompt-overlay');
  if (overlay) overlay.hidden = true;
  COMBINE_STAKE_PROMPT = null;
}

// Fires only when verifyCombineBroadcast() resolves domain-confirmed AND the
// combine was opened from a worker tile (tileContext set in handleSlotEditorClick's
// 'combine-tile' branch) -- the worker-table door and the Idle Cards table never
// carry tileContext, so neither ever sees this prompt.
function openCombineStakePrompt(tileContext, survivorUid, survivorCopy) {
  if (!tileContext || !survivorUid) return;
  ensureCombineStakePromptOverlay();
  const overlay = document.getElementById('combine-stake-prompt-overlay');
  const row = BOARD?.plots.find((plot) => plot.deed_uid === tileContext.deedUid);
  const group = row ? editorGroups(row).find((item) => item.id === tileContext.groupId) : null;
  const outgoingName = group?.slots[tileContext.slotIndex]?.name || 'the card currently staked here';
  const slotLabel = group ? editorSlotLabel(group, tileContext.slotIndex) : 'this slot';
  // Captured NOW (after domain-confirmed), not at combine-preview time -- this is the
  // earliest moment the client can honestly claim the combine is real, and it is what
  // the capture-freshness gate (ceres/capture_freshness.py::capture_is_newer_than
  // called from routes_op_preview.py) compares the next capture's timestamp against.
  COMBINE_STAKE_PROMPT = {
    deedUid: tileContext.deedUid, groupId: tileContext.groupId, slotIndex: tileContext.slotIndex,
    survivorUid, survivorCopy: survivorCopy || null, combineVerifiedAt: new Date().toISOString(),
  };
  document.getElementById('combine-stake-prompt-body').innerHTML =
    `<p>The combine is confirmed. Stake the surviving card into ${esc(slotLabel)}, replacing <strong>${esc(outgoingName)}</strong>? Or keep it off the plot for now?</p>`;
  overlay.hidden = false;
  overlay.querySelector('.hazard-close')?.focus();
}

export async function handleCombineStakePromptAction(action) {
  const kind = action.dataset.combineStakeAction;
  const prompt = COMBINE_STAKE_PROMPT;
  closeCombineStakePrompt();
  if (kind !== 'stake' || !prompt) return;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === prompt.deedUid);
  const group = row ? editorGroups(row).find((item) => item.id === prompt.groupId) : null;
  const outgoing = group?.slots[prompt.slotIndex] || null;
  if (!row || !group) {
    stageToast('This plot is no longer on the board -- reload and stake the combined survivor manually from the picker.', true);
    return;
  }
  // Same staging call the "replace" picker choice uses (stageForSlot/mutateEditorSlot)
  // never a second staging path -- the real PP is server-priced when the checkout
  // preview is built, same as every other staged move (stageMovePayload's own comment
  // `base`/`delta` here are display estimates only). `delta` is 0 -- unlike the picker
  // this move never went through /api/plots/{deed}/candidates, so no client-side
  // estimate exists yet; the checkout preview supplies the real number before signing.
  stageForSlot(row.deed_uid, group.id, prompt.slotIndex, 'replace', prompt.survivorUid, 0, outgoing?.asset_ref || null, 'Combine survivor', null, null, null, prompt.survivorCopy);
  const entry = CERES_STAGE.find((item) => item.deed_uid === row.deed_uid && item.group_id === group.id && item.slot_index === prompt.slotIndex);
  // Rides on the stage entry (excluded from stageMovePayload's move body, same as
  // `base`/`cooldown`) so requestOperationPreview can surface it at the REQUEST BODY
  // level -- the capture-freshness gate this reuses reads `combine_verified_at` there
  // never per-move (ceres/routes_op_preview.py::_preview).
  if (entry) entry.combine_verified_at = prompt.combineVerifiedAt;
  if (prompt.survivorCopy) mutateEditorSlot(row, group, prompt.slotIndex, editorOccupant(prompt.survivorCopy, group.id));
  updateMountedRow(row.deed_uid);
  if (typeof scrollPlotAfterSlotAdd === 'function') scrollPlotAfterSlotAdd(row.deed_uid);
  stageToast('Staged: the combined survivor will replace the outgoing card here once you sign it in Changes.');
}

// Parses `_known_block()`'s "cannot combine selected Card IDs: <uid> <reason>; ..."
// message (fides_cards/ops.py) into per-uid reasons -- the ONLY place this dialog
// interprets the server's authoritative eligibility text, so a wording change
// there is a visible new-block reason here, never a silent parse failure (an
// unparsed reason still refuses the whole batch, exactly as the server intends
// it just can't be attributed to one copy for the pre-tick).
function parseCombineBlockedUids(message) {
  const marker = 'cannot combine selected Card IDs: ';
  const idx = typeof message === 'string' ? message.indexOf(marker) : -1;
  const found = new Map();
  if (idx < 0) return found;
  message.slice(idx + marker.length).split('; ').forEach((segment) => {
    const spaceAt = segment.indexOf(' ');
    if (spaceAt < 0) return;
    found.set(segment.slice(0, spaceAt), segment.slice(spaceAt + 1));
  });
  return found;
}

// The combine editor keeps the single shared card graphic beside the outcome
// ledger; the ledger carries the before/after change without duplicate art.
// Dispatcher only (v0.7.81, work/combine-modal-redesign/arrive-spec.md): once
// verifyCombineBroadcast() reaches domain-confirmed it sets COMBINE_ARRIVAL and
// this branches to the "Arrive" state instead of the pre-sign editor -- the SAME
// overlay, never auto-closed. COMBINE_ARRIVAL stays null for every other verdict
// (loading, refusal, domain-pending/could-not-verify/mismatch), so those still
// render the ordinary pre-sign body below, unchanged.
function renderCombineDialog() {
  const body = document.getElementById('combine-dialog-body');
  const title = document.getElementById('combine-dialog-title');
  if (!body || !title || !COMBINE_DIALOG) return;
  if (COMBINE_ARRIVAL) { renderCombineArrival(body, title); return; }
  renderCombineDialogEditor(body, title);
}

function renderCombineDialogEditor(body, title) {
  const dialog = COMBINE_DIALOG;
  const { name } = dialog;
  const state = COMBINE_PREVIEW;
  const loading = state?.loading;
  const refusal = state?.error;
  const preview = state?.payload;
  const available = combineAvailableCopies(dialog);
  const ledger = combineLedgerData(dialog);
  const survivorUid = preview?.predicted_survivor_uid
    ? String(preview.predicted_survivor_uid)
    : (ledger?.survivor ? String(ledger.survivor.uid || ledger.survivor.asset_ref) : null);

  // The tier-mismatch stop (state 05, requestBroadcastPromise()'s refusal) is a
  // CLIENT-side stop, never a server refusal -- its own notice (combine_key_tier_refused)
  // is used verbatim here instead of being wrapped in "Combine refused by the
  // server: …", which would misstate where the refusal came from.
  const refusalText = state?.broadcastStalled || state?.tierMismatch
    ? (state.detail?.message || '')
    : combineNoticeText('combine_refusal', { reason: combineReason(state), observed_at: combineObservedAt(state) });
  const selectedCount = COMBINE_SELECTION.size;
  const signLabel = combineNoticeText('combine_sign');
  const previewText = selectedCount < 2
    ? combineNoticeText('combine_select')
    : loading
      ? combineNoticeText('combine_preview_loading')
      : refusal
        ? refusalText
        : '';
  const gateReason = refusal ? refusalText : previewText || signLabel;
  const canSign = !!preview?.buildable && !loading && !state?.broadcastStalled && !COMBINE_VERIFY?.loading && !COMBINE_SIGN_STEP;

  const identityArt = ledger ? editorCardImageHtml(ledger.survivor, { alt: name }).replace('class="eos-portrait-fill"', 'class="eos-portrait-fill combine-hero-art"') : '';
  const identityHtml = ledger
    ? `<div class="chain-hero combine-identity combine-editor-left">${identityArt}<div><strong>${esc(name)}</strong>${foilBadgeHtml(ledger.survivor, 'combine-hero-foil')}<small>Choose the copies to combine</small></div></div>`
    : `<div class="chain-hero combine-identity combine-editor-left"><div><strong>${esc(name)}</strong></div></div>`;

  const quantity = dialog.quantity || selectedCount;
  const ledgerHtml = ledger ? (() => {
    const beforePpText = ledger.beforePp != null ? ` · ${num(ledger.beforePp, 0)} PP` : '';
    const afterPpText = ledger.afterPp != null ? ` · ${num(ledger.afterPp, 0)} PP` : '';
    const deltaPp = ledger.beforePp != null && ledger.afterPp != null ? ledger.afterPp - ledger.beforePp : null;
    const maxBadge = ledger.isMaxed ? ' <span class="combine-maxed">MAX</span>' : '';
    return `<div class="combine-ledger combine-ledger-secondary">` +
      `<div class="combine-ledger-col"><div class="combine-ledger-label">Now</div>` +
      `<div class="combine-ledger-big">Lv ${esc(ledger.beforeLevel ?? '—')}</div>` +
      `<div class="combine-ledger-sm">${num(ledger.beforeBcx, 0)} CC${beforePpText}</div></div>` +
      `<div class="combine-ledger-arrow" aria-hidden="true">→</div>` +
      `<div class="combine-ledger-col is-after"><div class="combine-ledger-label">After combining</div>` +
      `<div class="combine-ledger-big">Lv ${esc(ledger.afterLevel ?? '—')}${maxBadge}</div>` +
      `<div class="combine-ledger-sm">${num(ledger.afterBcx, 0)} CC${afterPpText}</div>` +
      `${deltaPp != null && deltaPp > 0 ? `<div class="combine-ledger-delta">+${num(deltaPp, 0)} PP</div>` : ''}` +
      `</div></div>`;
  })() : '';

  // Amount control: presets for the two things anyone actually wants, a
  // stepper for the rest. This is what makes a 400-copy printing survivable
  // the list never grows because the list is no longer the control.
  const presetQty = combinePresetQuantities(dialog);
  // "Proposed set" when the dialog was opened from Find combinables; : an explicit selection (proposed
  // to max) marks its own button, a quantity marks the button of the same size.
  const presetButtons = [
    dialog.proposed ? { key: 'proposed', label: 'Proposed set', qty: dialog.proposed.length } : null,
    presetQty.plusOne != null ? { key: 'plus_one', label: '+1 level', qty: presetQty.plusOne } : null,
    presetQty.toMax != null ? { key: 'to_max', label: 'To max level', qty: presetQty.toMax } : null,
    { key: 'everything', label: `Everything (${presetQty.everything})`, qty: presetQty.everything },
  ].filter(Boolean).map((p) => `<button type="button" class="combine-preset${(dialog.preset ? p.key === dialog.preset : p.qty === quantity && p.key !== 'proposed' && p.key !== 'to_max') ? ' is-on' : ''} ceres-btn ceres-btn--choice" data-control-density="compact" data-combine-action="preset" data-combine-preset="${p.key}"><span class="ceres-btn__check" aria-hidden="true"></span>${esc(p.label)}</button>`).join('');
  const amountHtml = `<div class="combine-amount"><div class="combine-presets">${presetButtons}</div>` +
    `<div class="combine-stepper">` +
    `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-mode="icon" data-combine-action="qty" data-combine-qty="dec" aria-label="Fewer"${quantity <= 2 ? ' disabled' : ''}>−</button>` +
    `<span class="combine-stepper-val">${num(quantity, 0)}</span>` +
    `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-mode="icon" data-combine-action="qty" data-combine-qty="inc" aria-label="More"${quantity >= available.length ? ' disabled' : ''}>+</button></div></div>`;

  const ceilingHtml = ledger?.survivor?.bcx_max != null && ledger.afterBcx > ledger.survivor.bcx_max
    ? `<p class="combine-ceiling-caution">Above ${num(ledger.survivor.bcx_max, 0)} CC this card's PP stops rising — the extra copies would add no PP.</p>` : '';
  const destroyedCount = preview?.hazard ? (preview.hazard.destroyed_uids || []).length : null;
  const consequenceText = preview?.buildable && destroyedCount != null
    ? `One copy survives · ${destroyedCount} ${destroyedCount === 1 ? 'copy' : 'copies'} destroyed`
    : 'A successful combine keeps one copy and destroys the other selected copies.';
  const copyRowsHtml = combineCopyRowsHtml(dialog, preview, survivorUid);
  const footerHtml = `<div class="combine-actions"><button type="button" class="picker-chip ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-combine-action="close-dialog">Cancel</button>` +
    `<button type="button" class="slot-review combine-sign ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="commit destructive" data-combine-action="sign"${canSign ? '' : ' disabled'} aria-label="${esc(canSign ? signLabel : gateReason)}">${COMMIT_ICON}${esc(destroyedCount != null ? `${signLabel} — destroys ${destroyedCount}` : signLabel)}</button></div>` +
    `${combineVerificationHtml()}`;

  title.textContent = `Combine — ${name}`;
  body.innerHTML = `<div class="combine-editor-layout">${identityHtml}` +
    `<div class="combine-editor-result combine-editor-info">${ledgerHtml}<p class="combine-consequence">${esc(consequenceText)}</p>${ceilingHtml}` +
    `<p class="combine-preview-message ${refusal ? 'is-refusal' : ''}" role="${refusal ? 'alert' : 'status'}">${esc(previewText)}</p></div>` +
    `<div class="combine-editor-selection">${amountHtml}${copyRowsHtml}</div>` +
    `<div class="combine-editor-footer">${footerHtml}</div></div>`;
}

// The "Arrive" state (v0.7.81, work/combine-modal-redesign/arrive-spec.md): the
// ONLY thing a domain-confirmed combine renders from here on, in the SAME modal
// the pre-sign screen used -- never a second popup, never auto-closed. Leads
// with the card itself (the game's own combine dialog is the bar, per the
// maintainer), then the SAME before/after ledger the decision was made from, a plain
// confirmation line, destruction named quietly, and nothing but a Done button
// unless this came from a worker tile (openCombineStakePrompt already offers
// that choice -- see verifyCombineBroadcast's own comment on why it stays a
// separate, already-tested overlay rather than folding in here).
// The consumed side of the amplified arrival stage (maintainer, 2026-08-30: "the
// modal must clearly and loudly show the surviving card and the destroyed
// previous card in the smartest way possible") -- one small, visibly
// DESTROYED tile per input, never a bare count. Dimmed + desaturated + struck
// (ceres.css), arranged so the merge arrow points every one of them at the
// survivor -- "these became that" read off the layout, not off a number. The
// A13: names and foil identify the destroyed copies on screen.
// Card IDs remain internal and must not enter visible or accessibility text.
function combineArrivalConsumedCardHtml(card) {
  const art = editorCardImage(card);
  const cardName = card.card_name || card.name || 'destroyed card';
  const artInner = art
    ? `<img class="combine-arrival-consumed-art" src="${esc(art)}" alt="${esc(cardName)}" loading="lazy" onerror="this.closest('.combine-arrival-consumed-card')?.classList.add('is-none')">`
    : '';
  // `is-none` applied HERE too (not only via onerror) when no art URL is even
  // computable -- so a missing-art card gets its dashed-border/name fallback
  // (ceres.css) immediately, never a bare grayscaled box waiting on a load
  // that was never going to happen. Same class the onerror handler adds, so
  // both routes into "no art" land on one CSS state.
  return `<div class="combine-arrival-consumed-card${art ? '' : ' is-none'}"><div class="combine-arrival-consumed-tile">${artInner}<span class="combine-arrival-consumed-strike" aria-hidden="true"></span><span class="combine-arrival-consumed-name" aria-hidden="true">${esc(cardName)}</span></div>${foilBadgeHtml(card, 'combine-arrival-foil')}</div>`;
}

function renderCombineArrival(body, title) {
  const arrival = COMBINE_ARRIVAL;
  const { name, ledger, survivorCopy, destroyedCount, destroyedCopies } = arrival;
  const art = survivorCopy ? editorCardImage(survivorCopy) : null;
  const survivorName = survivorCopy?.card_name || survivorCopy?.name || name;
  // Never a bare removal on failure: the old `onerror="this.remove()"` deleted
  // the <img> with nothing behind it -- the exact "blank box" the maintainer
  // reported. Now onerror marks the SURVIVOR wrapper `is-none` (ceres.css) so
  // the always-present `.combine-arrival-art-fallback` div takes over, naming
  // the card, then removes the broken <img> so no broken-image glyph shows
  // through it.
  const artHtml = art
    ? `<img class="combine-arrival-art" src="${esc(art)}" alt="${esc(survivorName)}" loading="lazy" onerror="this.closest('.combine-arrival-survivor')?.classList.add('is-none'); this.remove();">`
    : '';
  const artFallbackHtml = `<div class="combine-arrival-art-fallback" aria-hidden="true">${esc(survivorName)}</div>`;
  const survivorFoilHtml = foilBadgeHtml(survivorCopy, 'combine-arrival-foil');
  const survivorLevelHtml = ledger?.afterLevel != null
    ? `<div class="combine-arrival-survivor-level">Lv ${esc(ledger.afterLevel)}${ledger.isMaxed ? ' <span class="combine-maxed">MAX</span>' : ''}</div>` : '';
  // The stage: destroyed inputs (small, struck, dimmed) -> a merge arrow ->
  // the large celebratory survivor -- the relationship legible at a glance
  // per the maintainer's "smartest way possible", without reading the ledger's
  // numbers at all. Empty (no destroyed copies resolved) collapses to just
  // the survivor, never a broken arrow pointing at nothing.
  const consumedCards = destroyedCopies || [];
  const stageHtml = `<div class="combine-arrival-stage">` +
    (consumedCards.length
      ? `<div class="combine-arrival-consumed-row" role="list" aria-label="Cards destroyed in this combine">${consumedCards.map(combineArrivalConsumedCardHtml).join('')}</div>` +
        `<div class="combine-arrival-merge-arrow" aria-hidden="true">→</div>`
      : '') +
    `<div class="combine-arrival-survivor${art ? '' : ' is-none'}">${artHtml}${artFallbackHtml}${survivorFoilHtml}${survivorLevelHtml}</div></div>`;
  const ledgerHtml = ledger ? (() => {
    const beforePpText = ledger.beforePp != null ? ` · ${num(ledger.beforePp, 0)} PP` : '';
    const afterPpText = ledger.afterPp != null ? ` · ${num(ledger.afterPp, 0)} PP` : '';
    const deltaPp = ledger.beforePp != null && ledger.afterPp != null ? ledger.afterPp - ledger.beforePp : null;
    const maxBadge = ledger.isMaxed ? ' <span class="combine-maxed">MAX</span>' : '';
    return `<div class="combine-ledger combine-arrival-ledger">` +
      `<div class="combine-ledger-col"><div class="combine-ledger-label">Was</div>` +
      `<div class="combine-ledger-big">Lv ${esc(ledger.beforeLevel ?? '—')}</div>` +
      `<div class="combine-ledger-sm">${num(ledger.beforeBcx, 0)} CC${beforePpText}</div></div>` +
      `<div class="combine-ledger-arrow" aria-hidden="true">→</div>` +
      `<div class="combine-ledger-col is-after"><div class="combine-ledger-label">Now</div>` +
      `<div class="combine-ledger-big">Lv ${esc(ledger.afterLevel ?? '—')}${maxBadge}</div>` +
      `<div class="combine-ledger-sm">${num(ledger.afterBcx, 0)} CC${afterPpText}</div>` +
      `${deltaPp != null && deltaPp > 0 ? `<div class="combine-ledger-delta">+${num(deltaPp, 0)} PP</div>` : ''}` +
      `</div></div>`;
  })() : '';
  const levelText = ledger?.afterLevel != null ? `Level ${ledger.afterLevel}${ledger.isMaxed ? ' MAX' : ''}` : 'a higher level';
  // Kept UNCONDITIONALLY (test_combine_arrival.py's existing guard reads this
  // exact line regardless of whether the destroyed cards themselves also
  // render above) -- the visual consumed-card row is an addition, not a
  // replacement for the compact count.
  const destroyedFallbackHtml = destroyedCount > 0
    ? `<p class="combine-arrival-destroyed">${destroyedCount} ${destroyedCount === 1 ? 'copy' : 'copies'} destroyed</p>` : '';
  const provisional = arrival.provisional === true;
  // GIGANTIC success statement (maintainer, 2026-08-30) -- dominates the panel
  // never body text. Provisional (broadcast succeeded, domain verdict not in
  // yet -- the internal rules's binding rule that a not-yet-propagated read is never
  // presented as confirmed) gets its own honest, still-loud headline rather
  // than borrowing the confirmed one.
  const headlineHtml = provisional
    ? `<p class="combine-arrival-headline is-provisional"><span class="combine-arrival-headline-icon" aria-hidden="true">⏳</span> Combined — confirming in game…</p>`
    : `<p class="combine-arrival-headline"><span class="combine-arrival-headline-icon" aria-hidden="true">✓</span> Combine confirmed!</p>`;
  const statusLineHtml = provisional
    ? `<p class="combine-arrival-confirm is-provisional">Broadcast sent. Your card is now ${esc(levelText)} -- verifying against the game now, this is not yet confirmed.</p>`
    : `<p class="combine-arrival-confirm">Confirmed in game. Your card is now ${esc(levelText)}.</p>`;
  const verificationHtml = provisional ? combineVerificationHtml() : '';

  title.textContent = provisional ? `Combining — ${name}` : `Combine confirmed — ${name}`;
  body.innerHTML = `<div class="combine-arrival${provisional ? ' is-optimistic' : ''}">${headlineHtml}${stageHtml}` +
    `<div class="combine-arrival-details">${ledgerHtml}` +
    `${statusLineHtml}` +
    `${destroyedFallbackHtml}` +
    `${verificationHtml}` +
    `<div class="combine-actions"><button type="button" class="picker-chip ceres-btn ceres-btn--secondary" data-control-density="compact" data-combine-action="close-dialog">Done</button></div>` +
    `</div></div>`;
}

async function requestCombinePreview() {
  if (COMBINE_SELECTION.size < 2) {
    COMBINE_PREVIEW = null;
    renderCombineDialog();
    return;
  }
  const uids = [...COMBINE_SELECTION.keys()];
  const previewRun = ++COMBINE_PREVIEW_RUN; // S7 / L4-04: only the newest request may write the preview
  COMBINE_VERIFY = null;
  COMBINE_PREVIEW = { loading: true, payload: null, error: null, detail: null, uids };
  renderCombineDialog();
  try {
    const response = await fetch(playerQueryAppend('/api/combine/preview'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uids }),
    });
    const detail = await response.json().catch(() => ({}));
    if (previewRun !== COMBINE_PREVIEW_RUN) return; // a newer selection superseded this answer
    if (!response.ok) {
      // Two shapes carry a per-copy reason: the state-reasons path (Staked on
      // Land / Unstaking, routes_combine.py::_state_reasons) is already
      // structured; the build_combine_op path is a joined message string
      // (parseCombineBlockedUids above). Either way, peel off exactly the uids
      // the server named and retry with what's left -- this is what turns "the
      // server refused the batch" into "these specific copies are ineligible"
      // which is the whole premise of pre-ticking only what will actually sign.
      const structural = new Map((detail.detail?.reasons || []).map((r) => [String(r.uid), r.reason]));
      const parsed = parseCombineBlockedUids(detail.detail?.message || detail.message);
      const newlyBlocked = new Map([...structural, ...parsed]);
      if (COMBINE_DIALOG && newlyBlocked.size) {
        let removedAny = false;
        newlyBlocked.forEach((reason, uid) => {
          if (COMBINE_SELECTION.has(uid)) {
            COMBINE_SELECTION.delete(uid);
            COMBINE_DIALOG.blocked.set(uid, reason);
            removedAny = true;
          }
        });
        if (removedAny) { await requestCombinePreview(); return; }
      }
      COMBINE_PREVIEW = { loading: false, payload: detail.preview || detail.detail?.preview || null, detail: detail.detail || detail, error: 'server refusal', uids };
    } else {
      COMBINE_PREVIEW = { loading: false, payload: detail, detail: null, error: null, uids };
    }
  } catch (error) {
    if (previewRun !== COMBINE_PREVIEW_RUN) return;
    COMBINE_PREVIEW = { loading: false, payload: null, detail: { message: String(error) }, error: 'request failed', uids };
  }
  if (previewRun !== COMBINE_PREVIEW_RUN) return;
  renderCombineDialog();
}

async function verifyCombineBroadcast(operation, txId, operationReceipt = null) {
  // Attempt count for state 05/06's "Confirming in game · attempt N" (mirrors
  // verifyBroadcast()'s chunk.verificationAttempt pattern) -- starts at 1 for a
  // freshly-signed txId, increments on every manual "Check again" against the
  // SAME txId, so the server can tell "still propagating" from "exhausted its
  // escalation threshold" (fides_land/verify.py's PENDING_ESCALATION_ATTEMPTS).
  const run = ++COMBINE_VERIFY_RUN;
  if (operationReceipt?.entryId) clearOperationPollTimer(operationReceipt.entryId);
  const attempt = (COMBINE_VERIFY?.txId === txId ? COMBINE_VERIFY.attempt : 0) + 1;
  COMBINE_SIGN_STEP = null;
  COMBINE_VERIFY = { loading: true, operation, txId, result: null, error: null, attempt, operationReceipt };
  renderCombineDialog();
  try {
    const response = await fetch(playerQueryAppend('/api/op-verify'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tx_id: txId, operations: [operation], verification_attempt: attempt, entry_id: operationReceipt?.entryId }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `verification HTTP ${response.status}`);
    if (run !== COMBINE_VERIFY_RUN) return;
    COMBINE_VERIFY = { loading: false, operation, txId, result, error: null, attempt, operationReceipt };
    if (result.state === 'domain-confirmed') {
      if (operationReceipt) {
        stopOperationPoll(operationReceipt.entryId);
        publishOperationReceipt({ ...operationReceipt, phase: 'confirmed', attempt, lastState: result.state, result, nextPollAt: null });
      }
      // Captured before the cleanup below clears COMBINE_SELECTION/COMBINE_PREVIEW
      // tileContext only exists for a door-2 (worker-tile) combine; survivorCopy is
      // the pre-combine record for the predicted survivor, used for its art/name
      // base_pp both here (the arrival card) and by the tile if the player chooses
      // to stake it (its bcx/level are stale post-combine, same caveat every other
      // staged-move optimistic render already carries until the real capture lands).
      const tileContext = COMBINE_DIALOG?.tileContext || null;
      const survivorUid = COMBINE_PREVIEW?.payload?.predicted_survivor_uid || null;
      const survivorCopy = survivorUid
        ? (COMBINE_DIALOG?.copies || []).find((c) => String(c.uid || c.asset_ref) === String(survivorUid)) || null
        : null;
      // Arrival state (the internal rules "Arrive" spec, maintainer report 2026-08-30): snapshot
      // the SAME ledger and destroyed-count the pre-sign screen showed, before this
      // branch clears COMBINE_SELECTION/COMBINE_PREVIEW below -- so what the player
      // reads after signing is provably the same object as what they agreed to.
      const arrivalLedger = COMBINE_DIALOG ? combineLedgerData(COMBINE_DIALOG) : null;
      const destroyedUidSet = new Set(COMBINE_PREVIEW?.payload?.hazard?.destroyed_uids || []);
      const destroyedCount = destroyedUidSet.size;
      // The amplified arrival state (maintainer, 2026-08-30: "the modal must clearly and
      // loudly show the surviving card and the destroyed previous card in the
      // smartest way possible") needs the destroyed copies AS CARDS, not just a
      // count -- pulled from COMBINE_DIALOG.copies, the same pre-combine records
      // the pre-sign editor's own destroyed-row derivation, snapshotted here for
      // the same reason arrivalLedger is
      // this is provably the same set the player agreed to, read before anything
      // below clears COMBINE_DIALOG's usefulness.
      const destroyedCopies = (COMBINE_DIALOG?.copies || [])
        .filter((c) => destroyedUidSet.has(String(c.uid || c.asset_ref)));
      // The provisional arrival is now the real verdict. Rebuild its display
      // from the same pre-combine source data, and drop provisional only here.
      COMBINE_ARRIVAL = {
        name: COMBINE_DIALOG?.name || 'card',
        ledger: arrivalLedger,
        survivorUid,
        survivorCopy,
        destroyedCount,
        destroyedCopies,
        tileContext,
        provisional: false,
        operationReceipt,
      };
      applyCombineConfirmedUpdate(COMBINE_DIALOG, COMBINE_PREVIEW?.payload, arrivalLedger, survivorUid);
      COMBINE_SELECTION.clear();
      COMBINE_PREVIEW = null;
      COMBINE_VERIFY = null;
      // Unlike the old delete-and-close path, the overlay stays open and
      // COMBINE_DIALOG stays set -- renderCombineDialog() now branches on
      // COMBINE_ARRIVAL and never reads the dialog's (now-stale) copy list again.
      // "Zero UI feedback... the most unsatisfying combination in 8 years" (maintainer
      // 2026-08-30) was the deletion itself; the player reads the result and
      // closes it themselves.
      renderCombineDialog();
      stageToast(combineNoticeText('combine_confirmed'));
      // The confirmed combine changed the account's card collection (bcx/level/uid)
      // GET /api/board and GET /api/idle-cards both read only the on-disk
      // snapshot, which still shows pre-combine values until the server's own
      // re-capture (triggered server-side, see routes_op_preview.py's
      // `combine_refresh`) lands. Nulling IDLE_CARDS_CACHE and re-fetching right
      // here, before that capture finishes, would just re-serve the same stale
      // snapshot -- settleCombineRefresh() polls capture status and only refreshes
      // once it actually lands.
      if (COMBINE_HOST?.onConfirmed) COMBINE_HOST.onConfirmed({ combineRefresh: result.combine_refresh });  // the Cards page
      else settleCombineRefresh(result.combine_refresh);
      if (tileContext) openCombineStakePrompt(tileContext, survivorUid, survivorCopy);
    } else if (result.state === 'game-refused') {
      if (operationReceipt) {
        stopOperationPoll(operationReceipt.entryId);
        const reason = result.notices?.find((item) => item?.code === 'verification_game_refused')?.reason
          || result.game?.evidence?.reason || result.game?.evidence?.error || 'The game refused this operation.';
        publishOperationReceipt({ ...operationReceipt, phase: 'failed', attempt, lastState: result.state, result, failureDetail: reason, nextPollAt: null });
      }
      COMBINE_ARRIVAL = null;
      renderCombineDialog();
    } else if (result.state === 'domain-mismatch') {
      if (operationReceipt) {
        stopOperationPoll(operationReceipt.entryId);
        publishOperationReceipt({ ...operationReceipt, phase: 'failed', attempt, lastState: result.state, result, failureDetail: 'Nothing was combined. No cards were destroyed.', nextPollAt: null });
      }
      // A mismatch is terminal and needs no cache rollback because the
      // provisional result never mutated card-derived state.
      COMBINE_ARRIVAL = null;
      renderCombineDialog();
    } else {
      // domain-pending / domain-could-not-verify / unrecognised: never a
      // confirmed "no", so the provisional arrival stays exactly as shown -- but
      // "not confirmed yet" is never hidden behind a screen that already
      // looks finished (the internal rules's four-verdict split, "domain-pending is
      // not confirmation").
      if (COMBINE_ARRIVAL) COMBINE_ARRIVAL.verdict = result;
      if (operationReceipt) {
        const updatedReceipt = publishOperationReceipt({
          ...operationReceipt,
          phase: result.state === 'domain-could-not-verify' ? 'unknown' : 'in_flight',
          attempt,
          lastState: result.state,
          result,
        });
        if (scheduleOperationPoll(updatedReceipt, updatedReceipt.pollVerify || ((receipt) => verifyCombineBroadcast(operation, receipt.txId, receipt))) === false) {
          COMBINE_VERIFY.error = 'Not confirmed yet. Automatic checks have paused. Check again to read the result without sending another transaction.';
        }
      }
      renderCombineDialog();
    }
  } catch (error) {
    if (run !== COMBINE_VERIFY_RUN) return;
    COMBINE_VERIFY = { loading: false, operation, txId, result: null, error: String(error), attempt, operationReceipt };
    // A failed verify request is exactly as ambiguous as domain-could-not
    // verify -- the broadcast may well have succeeded. Never roll back on a
    // mere request failure; only a positive domain-mismatch verdict does that.
    if (COMBINE_ARRIVAL) COMBINE_ARRIVAL.verdict = { state: 'client-error', error: String(error) };
    if (operationReceipt) {
      const updatedReceipt = publishOperationReceipt({ ...operationReceipt, phase: 'unknown', attempt, lastState: 'verification-error', failureDetail: String(error) });
      if (scheduleOperationPoll(updatedReceipt, updatedReceipt.pollVerify || ((receipt) => verifyCombineBroadcast(operation, receipt.txId, receipt))) === false) {
          COMBINE_VERIFY.error = 'Not confirmed yet. Automatic checks have paused. Check again to read the result without sending another transaction.';
        }
    }
    renderCombineDialog();
  }
}

// Signs directly from the combine dialog itself (v0.7.73) -- the redesign folds
// what used to be a second confirmation popup (openCombineHazard, removed) into
// this same modal: the outcome ledger and every selected Card ID are visible
// before the button is enabled, so the hazard contract is satisfied without a
// second panel.
// both signing paths rebuild from the live collection at the last gate.
export async function rebuildCombineForSigning(uids, planReview = null) {
  const response = await fetch(playerQueryAppend('/api/combine/build'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uids, ...(planReview ? { plan_review: planReview } : {}) }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.operation) throw new Error(body.detail?.message || body.detail || `build HTTP ${response.status}`);
  return body;
}

export function combineReviewMatches(reviewed, fresh) {
  const facts = value => JSON.stringify([value?.predicted_survivor_uid, value?.resulting_bcx, value?.hazard?.destroyed_uids]);
  return !!fresh && facts(reviewed) === facts(fresh);
}

export async function confirmCombineBroadcast() {
  const preview = COMBINE_PREVIEW?.payload;
  if (!preview?.buildable || COMBINE_SELECTION.size < 2) return;
  const uids = [...COMBINE_SELECTION.keys()];
  const signButton = document.querySelector('#combine-dialog-body .combine-sign');
  if (signButton) signButton.disabled = true;
  // State 05 (Sign) step 1: waiting on Keychain -- rendered by combineProgressHtml()
  // off COMBINE_SIGN_STEP, never a frozen dialog. The hero card above it stays
  // exactly as it was (renderCombineDialogEditor still renders it), so the player
  // keeps seeing what is being combined throughout the wait.
  COMBINE_VERIFY = null;
  COMBINE_SIGN_STEP = { step: 'keychain', txId: null };
  renderCombineDialog();
  try {
    const body = await rebuildCombineForSigning(uids);
    if (!combineReviewMatches(preview, body.preview)) {
      if (body.preview) COMBINE_PREVIEW.payload = body.preview;
      throw new Error('The selected cards changed. Review the updated combine result before signing. Nothing was sent.');
    }
    const operationReceipt = await beginSharedOperation({
      account: preview.account || CURRENT_PLAYER,
      operations: [body.operation],
      family: 'combine',
      context: `Combine ${COMBINE_DIALOG?.name || 'cards'}`,
      onResponse: (response) => {
        const callbackTxId = response?.result?.id || response?.result?.tx_id || response?.tx_id;
        if (response?.success && callbackTxId) {
          COMBINE_SIGN_STEP = { step: 'chain', txId: callbackTxId };
          renderCombineDialog();
        }
      },
    });
    const txId = operationReceipt.txId;
    operationReceipt.pollVerify = (receipt) => verifyCombineBroadcast(body.operation, receipt.txId, receipt);
    // Step 2: On chain -- the tx id is now known, rendered before the domain
    // check (verifyCombineBroadcast's own loading state) even starts.
    COMBINE_SIGN_STEP = { step: 'chain', txId };
    // The transaction is on chain, but its game effect remains unknown until
    // domain verification confirms it.
    const ledgerNow = COMBINE_DIALOG ? combineLedgerData(COMBINE_DIALOG) : null;
    const survivorUidNow = preview.predicted_survivor_uid ? String(preview.predicted_survivor_uid) : null;
    const survivorCopyNow = survivorUidNow
      ? (COMBINE_DIALOG?.copies || []).find((c) => String(c.uid || c.asset_ref) === survivorUidNow) || null
      : null;
    const destroyedUidSetNow = new Set((preview.hazard?.destroyed_uids || []).map(String));
    const destroyedCopiesNow = (COMBINE_DIALOG?.copies || []).filter((c) => destroyedUidSetNow.has(String(c.uid || c.asset_ref)));
    COMBINE_ARRIVAL = {
      name: COMBINE_DIALOG?.name || 'card',
      ledger: ledgerNow,
      survivorUid: survivorUidNow,
      survivorCopy: survivorCopyNow,
      destroyedCount: destroyedUidSetNow.size,
      destroyedCopies: destroyedCopiesNow,
      tileContext: COMBINE_DIALOG?.tileContext || null,
      provisional: true,
      pollStartedAt: Date.now(),
      operationReceipt,
    };
    renderCombineDialog();
    await verifyCombineBroadcast(body.operation, txId, operationReceipt);
  } catch (error) {
    COMBINE_SIGN_STEP = null;
    if (signButton) signButton.disabled = !error?.inclusionUnobserved && !error?.ambiguousBroadcast ? false : true;
    // ACTIVE-vs-Posting hard refusal (the internal rules: sm_combine_cards is Posting
    // 294/294 observed) -- requestBroadcastPromise() rejects with `tierMismatch`
    // when Keychain's response echoes a signing key other than Posting; this
    // renders its own distinct, non-server-refusal copy rather than falling
    // through to the generic combine_refusal template.
    const message = error?.tierMismatch
      ? combineNoticeText('combine_key_tier_refused', { key: error.keyTier || 'Active' })
      : String(error);
    COMBINE_PREVIEW = {
      ...COMBINE_PREVIEW,
      error: String(error),
      detail: { message: error?.inclusionUnobserved || error?.ambiguousBroadcast ? error.message : message },
      tierMismatch: !!error?.tierMismatch,
      broadcastStalled: !!(error?.inclusionUnobserved || error?.ambiguousBroadcast),
    };
    renderCombineDialog();
  }
}

export async function handleCombineAction(action) {
  const kind = action.dataset.combineAction;
  if (kind === 'open-group') { openCombineGroupDialog(action.dataset.combineGroup); return; }
  // Quantity control (v0.7.73 redesign) -- replaces the old per-copy checkbox
  // list ('toggle-copy', retired). A preset or a stepper click always goes
  // through combineApplyQuantity(), the single place that turns "how many"
  // into explicit Card IDs, so a preset and a manually-stepped-to quantity of
  // the same number always burn the same cards.
  if (kind === 'preset') {
    if (!COMBINE_DIALOG) return;
    const preset = action.dataset.combinePreset;
    // the two explicit selections; the others stay quantities in consumption order
    if (preset === 'to_max' || preset === 'proposed') {
      const copies = preset === 'to_max' ? combineToMaxCopies(COMBINE_DIALOG) : COMBINE_DIALOG.proposed;
      if (!copies || !combineApplySelection(COMBINE_DIALOG, copies, preset)) return;
    } else {
      const presets = combinePresetQuantities(COMBINE_DIALOG);
      const target = { plus_one: presets.plusOne, everything: presets.everything }[preset];
      if (target == null) return;
      combineApplyQuantity(COMBINE_DIALOG, target);
    }
    COMBINE_PREVIEW = null;
    COMBINE_VERIFY = null;
    renderCombineDialog();
    if (COMBINE_SELECTION.size >= 2) requestCombinePreview();
    return;
  }
  if (kind === 'qty') {
    if (!COMBINE_DIALOG) return;
    const step = action.dataset.combineQty === 'inc' ? 1 : -1;
    const current = COMBINE_DIALOG.quantity || COMBINE_SELECTION.size;
    combineApplyQuantity(COMBINE_DIALOG, current + step);
    COMBINE_PREVIEW = null;
    COMBINE_VERIFY = null;
    renderCombineDialog();
    if (COMBINE_SELECTION.size >= 2) requestCombinePreview();
    return;
  }
  if (kind === 'close-dialog') { closeCombineDialog(); return; }
  if (kind === 'sign') { confirmCombineBroadcast(); return; }
  if (kind === 'confirm') { confirmCombineBroadcast(); return; }
  if (kind === 'verify' && COMBINE_VERIFY?.operation && COMBINE_VERIFY?.txId) {
    const operationReceipt = COMBINE_ARRIVAL?.operationReceipt || null;
    const currentReceipt = operationReceipt?.entryId ? OPERATION_RECEIPTS.get(operationReceipt.entryId) || operationReceipt : operationReceipt;
    verifyCombineBroadcast(COMBINE_VERIFY.operation, COMBINE_VERIFY.txId, currentReceipt);
  }
}

// the idle editor and signing flow keep their existing behavior.
export function openCombinePlanDialog(title, html) {
  ensureCombineDialogOverlay();
  COMBINE_DIALOG_RETURN_FOCUS = document.activeElement;
  document.getElementById('combine-dialog-overlay').classList.add('is-plan-mode');
  COMBINE_DIALOG = { mode: 'plan' };
  COMBINE_ARRIVAL = null;
  COMBINE_PREVIEW = null;
  document.getElementById('combine-dialog-title').textContent = title;
  document.getElementById('combine-dialog-body').innerHTML = html;
  document.getElementById('combine-dialog-overlay').hidden = false;
  document.getElementById('combine-dialog-overlay').querySelector('.hazard-close')?.focus();
}

// the Cards page's Find combinables opens this dialog by print -- /?combine=<card_detail_id>-<edition>-<foil> (no
// Card ID in the address). Once the board is shown, it reads that print's copies and proposed set (GET
// api/cards/combinables?print=..., the stored snapshot only) and opens the dialog with the set preselected -- on the idle
// cards' copies when they are read, else on the copies of that answer; signing is the dialog's own unchanged flow. The parameter is then cleared, so a reload does not reopen it.
export const COMBINE_ADDRESS_WAIT_MS = 5000;
export function combinePrintFromAddress(search) {
  const wanted = new URLSearchParams(search || '').get('combine');
  const match = /^(\d+)-(\d+)-([0-4])$/.exec(String(wanted || ''));
  return match ? { id: wanted, card_detail_id: Number(match[1]), edition: Number(match[2]), foil: Number(match[3]) } : null;
}

async function idleCardsReady(limitMs) {
  const until = Date.now() + limitMs;
  while (S.IDLE_CARDS_CACHE === null && Date.now() < until) {
    prefetchIdleCardsCache();
    await new Promise((resolve) => { setTimeout(resolve, 250); });
  }
  return S.IDLE_CARDS_CACHE !== null;
}

// The dialog's copies from the Find combinables answer, for a board whose idle cards are not read yet (their read waits
// for a complete collection): every own copy of the print, the blocked ones with the server's reason. The dialog's own
// preview and build re-check every copy on the server before anything can be signed.
export function combineCopiesFromGroup(group) {
  const p = group?.print;
  if (!p) return [];
  return (group.copies || []).filter((c) => c && c.uid).map((c) => ({
    uid: String(c.uid), asset_ref: String(c.uid), name: p.name, card_detail_id: p.card_detail_id, edition: p.edition, foil: p.foil,
    gold: p.foil === 1 || p.foil === 2, card_set: p.card_set, rarity: p.rarity, bcx: c.bcx, bcx_max: p.bcx_max, level: c.level,
    level_bcx_thresholds: p.thresholds || null, base_pp: c.land_base_pp, available: !!c.combinable,
    blocked_reason: (c.reasons || []).map((r) => r.text).join('; ') || null, status: c.status || null, plot: c.plot || null,
  }));
}

export async function openCombineFromPrint(print) {
  let group = null;
  try {
    const response = await fetch(playerQueryAppend(`/api/cards/combinables?print=${encodeURIComponent(print.id)}`), { cache: 'no-store' });
    const body = response.ok ? await response.json() : null;
    group = (body?.groups || []).find((g) => g?.print?.id === print.id) || null;
  } catch (_) { group = null; }  // the idle cards alone still open the dialog on its fit-to-max default
  const key = combineGroupKey({ card_detail_id: print.card_detail_id, edition: print.edition, foil: print.foil, gold: print.foil === 1 || print.foil === 2 });
  const idle = (await idleCardsReady(COMBINE_ADDRESS_WAIT_MS)) ? idleCombineGroups(S.IDLE_CARDS_CACHE).get(key) : null;
  const copies = idle && idle.length >= 2 ? idle : combineCopiesFromGroup(group);
  if (!copies || copies.length < 2) { stageToast('This card has no two copies to combine on this board.', true); return false; }
  IDLE_COMBINE_GROUPS.set(key, copies);
  openCombineGroupDialog(key, { preselect: group?.sets?.[0]?.uids || null });
  return true;
}

// Find combinables on the
// Cards page opens this same dialog in place, loaded on the first Combine press (card-combinables.js; wallet.js first). The
// copies are the Find combinables answer's own (combineCopiesFromGroup; no board, no /api/idle-cards read), the proposed set
// is preselected, and the dialog's own preview, build, signing and verification run unchanged. The player is the address's
// ?player= (board.js's CURRENT_PLAYER, which playerQueryAppend sends). `host` is the page's hooks (COMBINE_HOST).
export function openCombineInPlace(group, host = null) {
  const p = group?.print;
  if (!p) return false;
  const copies = combineCopiesFromGroup(group);
  if (copies.length < 2) return false;
  COMBINE_HOST = host || COMBINE_HOST;
  const key = combineGroupKey({ card_detail_id: p.card_detail_id, edition: p.edition, foil: p.foil, gold: p.foil === 1 || p.foil === 2 });
  IDLE_COMBINE_GROUPS.set(key, copies);
  openCombineGroupDialog(key, { preselect: group.sets?.[0]?.uids || null });
  return !!COMBINE_DIALOG;
}

export function openCombineFromAddress() {
  if (typeof location === 'undefined' || typeof window === 'undefined') return;
  const print = combinePrintFromAddress(location.search);
  if (!print) return;
  window.addEventListener('ceres:board-revealed', () => {
    const url = new URL(location.href); url.searchParams.delete('combine'); history.replaceState(history.state, '', url);
    openCombineFromPrint(print);
  }, { once: true });
}
openCombineFromAddress();  // registered when the board's module graph loads (the Cards page never loads it)
