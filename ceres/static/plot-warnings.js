// (maintainer picked looks A + C of the mockups): the warnings under an open plot.
// Owner, 2026-09-28: "Under a plot we have warning rows which are unclear to which card they relate
// and the order is also confusing ... The find a matching bloodline companion is actually very useful."
// One model: each warning is tied to the card(s) it belongs to (read from the staged removals), ranked
// by severity and carries ONE sentence. The sentence appears once, in the impact list below the plot
// each affected card wears an icon-only pill; hovering a list row lights its pills and vice versa.
// rank 1 = the plot can't produce (last power source removed, workers without slot power, hazards)
// rank 2 = bonuses lost (Toil and Kin pair broken, plot-wide ability loss)
// rank 3 = info (cap notices and anything else)
import { workerSubType } from './badges.js';
import { noticeMessage } from './board.js';
import { esc } from './format.js';
import { ASSET_BASE } from './state.js';

const POWER_ICON = `${ASSET_BASE}/land/no-power-source.svg`;
const BLOODLINE_ICON = `${ASSET_BASE}/abilities/ability_toil-and-kin.png`;
const INFO_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6M12 7.5v.5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>';
const RANK_TONE = { 1: 'danger', 2: 'warn', 3: 'info' };
const RANK_HEADING = { 1: 'Plot can’t produce', 2: 'Bonuses lost', 3: 'Info' };

function formatPpLoss(value) {
  const pp = Number(value);
  return Number.isFinite(pp) && Math.round(pp) < 0 ? `−${Math.abs(Math.round(pp)).toLocaleString('en-US')} PP` : '';
}

function removedCard(entry) {
  const asset = entry._previous_asset || entry._asset || {};
  return { groupId: entry.group_id, slotIndex: entry.slot_index, name: asset.name || entry._asset_name || 'This card', image: asset.image || null, asset };
}

function carriesBloodline(asset, subType) {
  return (asset?.land_abilities || []).some((entry) => Array.isArray(entry) && entry[0] === 'BLOODLINE' && String(entry[2]) === subType);
}

function carriesEnergized(asset) {
  return (asset?.land_abilities || []).some((entry) => (Array.isArray(entry) ? entry[0] : entry) === 'ENERGIZED');
}

// `staged`: this plot's staged entries. `removalNotices`: the de-duplicated server notices carried on
// its staged removals. `powerNotice`: the plot-wide "no worker-slot power" notice (already decided
// against the roster that remains after the staged changes), or null. `otherNotices`: cap notices.
export function buildPlotWarnings(deedUid, staged, removalNotices, powerNotice, otherNotices = []) {
  const removed = staged.filter((entry) => entry.action === 'unstake').map(removedCard);
  const warnings = [];
  const handled = new Set(['power_source_removed_warning', 'bloodline_pair_deactivated', 'plot_ability_loss']);
  const powerRemoved = removalNotices.some((notice) => notice.code === 'power_source_removed_warning');
  if (powerRemoved) {
    warnings.push({
      kind: 'power', rank: 1,
      cards: removed.filter((card) => ['runi', 'power_core'].includes(card.groupId) || carriesEnergized(card.asset)),
      sentence: 'The plot’s last power source: without it no worker here produces. Staging is still allowed.',
    });
  }
  // The same consequence as the card warning above: said once, there.
  if (powerNotice && !powerRemoved) {
    warnings.push({ kind: 'power', rank: 1, cards: [], sentence: noticeMessage(powerNotice) });
  }
  const bloodlines = removalNotices.filter((notice) => notice.code === 'bloodline_pair_deactivated');
  bloodlines.forEach((notice) => {
    const subType = String(notice.context?.sub_type || '');
    const holders = removed.filter((card) => carriesBloodline(card.asset, subType));
    const companions = removed.filter((card) => !carriesBloodline(card.asset, subType) && workerSubType(card.asset) === subType);
    const loss = formatPpLoss(notice.context?.pp_delta);
    warnings.push({
      kind: 'bloodline', rank: 2, cards: [...holders, ...companions],
      sentence: `Ends the ${subType} Toil and Kin pair for every worker on this plot${loss ? ` (${loss})` : ''}.`,
      action: { subType, label: notice.action?.label || `Find a matching ${subType} companion` },
    });
  });
  removalNotices.filter((notice) => notice.code === 'plot_ability_loss').forEach((notice) => {
    // The Toil and Kin warning already prints this PP change.
    if (bloodlines.some((b) => Number(b.context?.pp_delta) === Number(notice.context?.pp_delta))) return;
    const loss = formatPpLoss(notice.context?.pp_delta);
    if (loss) warnings.push({ kind: 'info', rank: 2, cards: [], sentence: `Plot-wide abilities drop by ${loss.slice(1)}.` });
  });
  [...removalNotices.filter((notice) => !handled.has(notice.code)), ...otherNotices].forEach((notice) => {
    const danger = ['hazard', 'refusal'].includes(notice.kind) || notice.severity === 'danger';
    warnings.push({ kind: 'info', rank: danger ? 1 : 3, cards: [], sentence: noticeMessage(notice) });
  });
  return warnings.sort((a, b) => a.rank - b.rank).map((warning, index) => ({ ...warning, id: `${deedUid}:${index}`, deedUid }));
}

function warningIconHtml(kind, cls) {
  if (kind === 'power') return `<img class="${cls}" src="${POWER_ICON}" alt="">`;
  if (kind === 'bloodline') return `<img class="${cls}" src="${BLOODLINE_ICON}" alt="">`;
  return `<span class="${cls} plot-warning-svg">${INFO_SVG}</span>`;
}

// The power column holds the Runi tile and its Power Core satellite: both land on that column.
function columnKeys(groupId, slotIndex) {
  return ['runi', 'power_core'].includes(groupId) ? ['runi:0', 'power_core:0'] : [`${groupId}:${slotIndex}`];
}

// Icon-only pills on the bottom edge of one tile column; the sentence lives in the list only.
export function plotWarningPillsHtml(warnings, groupId, slotIndex) {
  const keys = columnKeys(groupId, slotIndex);
  const mine = (warnings || []).filter((w) => w.cards.some((card) => keys.includes(`${card.groupId}:${card.slotIndex}`)));
  if (!mine.length) return '';
  return `<span class="plot-warning-pills">${mine.map((w) => `<span class="plot-warning-pill is-${RANK_TONE[w.rank]}" data-plot-warning="${esc(w.id)}" role="img" aria-label="${esc(RANK_HEADING[w.rank])}">${warningIconHtml(w.kind, 'plot-warning-pill-icon')}</span>`).join('')}</span>`;
}

// The impact list below the plot: grouped by severity, the cards' thumbnails and names, one sentence
// and the Bloodline finder as the one primary action.
export function plotWarningListHtml(warnings) {
  if (!warnings?.length) return '';
  let lastRank = null;
  const rows = warnings.map((w) => {
    const heading = w.rank === lastRank ? '' : `<li class="plot-warning-heading is-${RANK_TONE[w.rank]}">${esc(RANK_HEADING[w.rank])}</li>`;
    lastRank = w.rank;
    const thumbs = w.cards.length
      ? w.cards.map((card) => `<span class="plot-warning-thumb">${card.image ? `<img src="${esc(card.image)}" alt="">` : ''}</span>`).join('') + warningIconHtml(w.kind, 'plot-warning-kind')
      : `<span class="plot-warning-thumb is-plot">${warningIconHtml(w.kind, 'plot-warning-kind')}</span>`;
    const names = w.cards.length ? w.cards.map((card) => esc(card.name)).join(' + ') : 'Whole plot';
    const action = w.action
      ? `<span class="plot-warning-action"><button type="button" class="plot-warning-find ceres-btn ceres-btn--primary" data-control-density="compact" data-notice-action="open-bloodline-pairs" data-deed-uid="${esc(w.deedUid)}" data-sub-type="${esc(w.action.subType)}"><img class="plot-warning-find-icon" src="${BLOODLINE_ICON}" alt="">${esc(w.action.label)}</button></span>`
      : '';
    return `${heading}<li class="plot-warning-row is-${RANK_TONE[w.rank]}" data-plot-warning="${esc(w.id)}" data-plot-warning-kind="${esc(w.kind)}" data-deed-uid="${esc(w.deedUid)}"><span class="plot-warning-thumbs">${thumbs}</span><span class="plot-warning-text"><b>${names}</b><span>${esc(w.sentence)}</span></span>${action}</li>`;
  }).join('');
  return `<section class="plot-warning-list" aria-label="What the staged changes do to this plot"><ul>${rows}</ul></section>`;
}

// Linked hover: a list row lights its pills, a pill lights its row. A class toggle, no transition.
let linkedId = null;
function setLinked(id) {
  if (id === linkedId) return;
  if (linkedId) document.querySelectorAll('.is-plot-warning-linked').forEach((el) => el.classList.remove('is-plot-warning-linked'));
  linkedId = id;
  if (id) document.querySelectorAll(`[data-plot-warning="${CSS.escape(id)}"]`).forEach((el) => el.classList.add('is-plot-warning-linked'));
}
export function initPlotWarningHover() {
  document.addEventListener('mouseover', (event) => setLinked(event.target.closest?.('[data-plot-warning]')?.dataset.plotWarning || null));
}
