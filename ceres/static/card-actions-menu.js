// one shared action bar for the explicitly selected owned copies (step 2c), adjusted to the maintainer's
// decisions of 2026-10-28
// 1. Phoenix Ash is left out: no Ash action anywhere in the menu.
// 2. Stake and Unstake go through the board: Stake lands in the board's slot picker for that card, Unstake becomes
// an ordinary staged change in Changes (card-handoff.js). No per-card signing here.
// 3. Delegation is the maintainer's "Recall delegation" and the borrower's "Return card", apart from paid rentals.
// 4. Each action is active only once its operation is verified; the others stay greyed with a one-line reason
// (Burn for DEC until its reward handling is proven on a live transaction).
// This module never builds an operation, signs or broadcasts.
import { CARD_ACTION_ICONS } from './button-icons.js';
import { boardStagedPlans } from './board-staged-plans.js';
import { tipAttrs } from './tooltip.js';

// One registry glyph per action; no two actions share one (tests/ceres/test_ir9323_menu_actions.py).
export const CARD_ACTIONS = [
  ['stake', 'Stake', CARD_ACTION_ICONS['card-stake']], ['unstake', 'Unstake', CARD_ACTION_ICONS['card-unstake']],
  ['plot_link', 'Go to plot', CARD_ACTION_ICONS['card-plot']], ['delegate', 'Delegate', CARD_ACTION_ICONS['card-delegate']],
  ['recall_delegation', 'Recall delegation', CARD_ACTION_ICONS['card-recall']], ['return_card', 'Return card', CARD_ACTION_ICONS['card-return']],
  ['transfer', 'Transfer', CARD_ACTION_ICONS['card-transfer']], ['burn', 'Burn for DEC', CARD_ACTION_ICONS['card-burn']],
];
const HANDOFF = new Set(['stake', 'unstake']);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export async function readCardActionEligibility(copies, player) {
  const refs = copies.map((copy) => copy.asset_ref).filter((ref) => typeof ref === 'string' && ref);
  if (!refs.length) return new Map();
  const query = player ? `?player=${encodeURIComponent(player)}` : '';
  const response = await fetch(`/api/cards/eligibility${query}`, {
    method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ player, asset_refs: refs, staged_plans: boardStagedPlans(player) }),
  });
  if (!response.ok) throw new Error('Card actions could not be checked');
  const body = await response.json();
  return new Map((body.copies || []).map((copy) => [copy.asset_ref, copy.actions || {}]));
}

// One answer per action for the whole selection: active only when every selected copy qualifies.
export function selectedEligibility(action, selected, eligibility) {
  if (!selected.length) return { allowed: false, reason: 'Select a copy first' };
  if (action === 'stake' && selected.length !== 1) return { allowed: false, reason: 'Select one copy to stake' };
  if (action === 'plot_link' && selected.length !== 1) return { allowed: false, reason: 'Select exactly one staked copy' };
  for (const copy of selected) {
    const result = eligibility?.get(copy.asset_ref)?.[action];
    if (!result) return { allowed: false, reason: 'Card status could not be checked' };
    if (action === 'plot_link' ? !result.href : !result.available) {
      return { allowed: false, reason: result.reason || 'Card status could not be checked' };
    }
  }
  return { allowed: true, reason: '' };
}

// Where Stake and Unstake hand over to the board (card-handoff.js reads open= and copy=).
export function boardHandoffUrl(action, selected, player) {
  const params = new URLSearchParams();
  if (player) params.set('player', player);
  params.set('open', action);
  selected.forEach((copy) => params.append('copy', copy.asset_ref));
  return `/?${params}`;
}

export function sharedCardActionsHtml(copies, selectedIndexes, eligibility, note = '') {
  const selected = [...selectedIndexes].map((index) => copies[index]).filter(Boolean);
  const controls = CARD_ACTIONS.map(([key, label, icon]) => {
    const state = selectedEligibility(key, selected, eligibility);
    const tip = tipAttrs({ title: state.allowed ? label : `${label}: ${state.reason}` });
    const glyph = `<span class="cm-action-icon" aria-hidden="true">${icon}</span>`;
    const name = ` aria-label="${esc(state.allowed ? label : `${label} (unavailable: ${state.reason})`)}"`;
    if (state.allowed && key === 'plot_link') {
      const href = eligibility.get(selected[0].asset_ref)[key].href;
      return `<a class="ceres-btn ceres-btn--secondary cm-copy-action" data-control-density="compact" data-control-mode="icon" data-card-action="${key}" href="${esc(href)}"${name}${tip}>${glyph}</a>`;
    }
    // Greyed actions stay pressable (aria-disabled, not disabled) so a tap on a phone shows the reason in the note line.
    const greyed = state.allowed ? '' : ' aria-disabled="true"';
    return `<button type="button" class="ceres-btn ceres-btn--secondary cm-copy-action" data-control-density="compact" data-control-mode="icon" data-card-action="${key}"${name}${tip}${greyed}>${glyph}</button>`;
  }).join('');
  const status = note || (selected.length ? `${selected.length} selected` : 'Select a copy first');
  return `<div class="cm-copy-action-panel"><div class="cm-copy-actions" aria-label="Actions for selected copies">${controls}</div><span class="cm-copy-selection-count" aria-live="polite">${esc(status)}</span></div>`;
}

// A press in the bar: a greyed action explains itself in the note line; Stake/Unstake go to the board.
// Returns the note to show, or null when the press was not an action of this bar.
export function cardActionPress(button, { copies, selectedIndexes, eligibility, player, navigate = (url) => { location.href = url; } }) {
  const key = button?.dataset?.cardAction;
  const entry = CARD_ACTIONS.find(([action]) => action === key);
  if (!entry) return null;
  const selected = [...selectedIndexes].map((index) => copies[index]).filter(Boolean);
  const state = selectedEligibility(key, selected, eligibility);
  if (!state.allowed) return `${entry[1]}: ${state.reason}`;
  if (HANDOFF.has(key)) {
    navigate(boardHandoffUrl(key, selected, player));
    return key === 'stake' ? 'Opening the board to choose a plot and slot…' : 'Opening the board to stage the unstake…';
  }
  return '';
}

export function loadCardActionsMenuCss() {
  if (document.querySelector('link[data-card-actions-menu-css]')) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet'; link.href = '/static/card-cart.css'; link.dataset.cardActionsMenuCss = 'true';
  document.head.append(link);
}
