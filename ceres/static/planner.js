// phase 5 / / : the planner part of the Changes modal -- plans the live board dropped, listed with
// their reason until dismissed. checkout.js places it. (The cooling / Survival inventory was removed: .)
import { BOARD } from './board.js';
import { renderCheckoutSurface } from './checkout.js';
import { esc, formatPlotLabel, humanizeInstant } from './format.js';
import { dismissDroppedPlan, dropReasonText, droppedStagePlans } from './stage-plans.js';
import { revealStagedChange } from './stage-navigation.js';
import { territoryCellHtml } from './icons.js';

function plotLabel(deedUid) {
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  return formatPlotLabel(row?.plot || deedUid);
}
// the plot as a badge in its territory colour, and the badge is the button to the plot.
function plotBadge(deedUid) {
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  return `<button type="button" class="ch-plot-jump ch-plot-badge ceres-region-plaque ${territoryCellHtml(row?.territory, deedUid).cls}" data-control-kind="region-plaque" data-checkout-action="go" data-deed-uid="${esc(deedUid)}" aria-label="Show plot ${esc(plotLabel(deedUid))} on the board">${esc(plotLabel(deedUid))}</button>`;
}

const P = { shown: new Map() };
const PAGE = 25; // long lists show 25 at a time, never truncated

// What the Changes button needs when nothing is staged.
export function plannerCounts() { return { dropped: droppedStagePlans().length }; }

// Dropped plans: the live board made them impossible; listed with the reason until dismissed.
export function plannerDroppedHtml() {
  const dropped = droppedStagePlans();
  if (!dropped.length) return '';
  const rows = dropped.slice(0, P.shown.get('dropped') || PAGE).map((item, index) => {
    const canJump = item.reason !== 'plot_gone';
    const name = canJump
      ? `<button type="button" class="ch-link ceres-btn ceres-btn--secondary" data-control-density="compact" data-planner-open="${esc(item.deed_uid)}" data-nav-group="${esc(item.group_id)}" data-nav-slot="${esc(item.slot_index)}">${esc(item.name || 'A card')}</button>`
      : `<b>${esc(item.name || 'A card')}</b>`;
    return `<tr><td>${name}</td><td>${changeHtml(item)}</td><td><span class="ch-status is-bad">Dropped: ${esc(dropReasonText(item.reason))}</span></td><td class="ch-act"><button type="button" class="hm-btn is-ghost ch-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-planner-dismiss="${index}">Dismiss</button></td></tr>`;
  }).join('');
  const more = dropped.length > (P.shown.get('dropped') || PAGE) ? `<p><button type="button" class="hm-btn is-ghost ch-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-planner-more="dropped">Show more</button></p>` : '';
  return `<div class="hm-card"><h3 class="hm-sec-h">Dropped from your plan · ${dropped.length}</h3><table class="hm-table ch-table"><colgroup><col class="ch-col-card"><col class="ch-col-change"><col><col class="ch-col-act"></colgroup><tbody>${rows}</tbody></table>${more}</div>`;
}
function changeHtml(entry) {
  const what = entry.action === 'unstake' ? 'Unstake' : 'Stake';
  const where = String(entry.group_id || '').replace('_', ' ');
  const slot = entry.group_id === 'worker' && Number.isInteger(Number(entry.slot_index)) ? ` slot ${Number(entry.slot_index) + 1}` : '';
  return `${what} · plot ${plotBadge(entry.deed_uid)} · ${esc(where)}${esc(slot)}`;
}

export function boardReadHtml() {
  const w = BOARD?.generated_at ? humanizeInstant(BOARD.generated_at) : null;
  return w?.absolute ? `<p class="hm-hint">Based on the board read ${esc(w.absolute)}.</p>` : '';
}

export function initPlanner() {
  document.addEventListener('click', (event) => {
    const target = event.target.closest?.('#checkout-panel button');
    if (!target) return;
    if (target.dataset.plannerOpen) {
      revealStagedChange({ deedUid: target.dataset.plannerOpen, groupId: target.dataset.navGroup ?? null, slotIndex: target.dataset.navSlot ?? null });
      return;
    }
    if (target.dataset.plannerDismiss !== undefined) { dismissDroppedPlan(Number(target.dataset.plannerDismiss)); renderCheckoutSurface(); return; }
    if (target.dataset.plannerMore !== undefined) { const id = target.dataset.plannerMore; P.shown.set(id, (P.shown.get(id) || PAGE) + PAGE); renderCheckoutSurface(); return; }
  });
}
