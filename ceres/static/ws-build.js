// (maintainer: "Worksite changes must be reflected on the board... a red clock changing to a green tick when
// finished"; counter-proposal approved): while a plot's worksite is being built or changed, the Worksite column
// shows the NEW worksite's icon dimmed with a red clock in its corner and a live countdown in the tooltip; once
// the time is up the corner mark is a green tick ("finished -- feed the workers"); after the next board read
// (the new worksite live, `pending_change` gone) the cell is the normal icon again. Reads `row.pending_change`
// exactly as `badges.js::pendingChangeChip` does (`to`, `to_label`, `ready_at`, `stalled`) -- nothing recomputed.
import { formatCountdown } from './countdown.js';
import { esc, humanizeInstant } from './format.js';
import { resourceIconHtml } from './icons.js';
import { tipAttrs } from './tooltip.js';

const CLOCK_SVG = '<svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/></svg>';
const TICK_SVG = '<svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>';
const NO_TIP = { title: '' }; // the wrapper carries the tooltip, never the icon inside it
let TIMER = null;

function buildTip(label, readyAt, now) {
  const until = readyAt ? Date.parse(readyAt) : NaN;
  if (!Number.isFinite(until)) return { title: `${label} building`, body: readyAt === null ? 'stalled -- the plot produces no PP' : 'ready time unknown', peer: 'plot_pending' };
  if (until <= now) return { title: `${label} finished — feed the workers`, peer: 'plot_pending' };
  const when = humanizeInstant(readyAt);
  return { title: `${label} building`, body: `ready in ${formatCountdown(until - now)}${when?.absolute ? ` (${when.absolute})` : ''}`, peer: 'plot_pending' };
}

// The Worksite cell: the build-in-progress wrapper when a change is under way, the plain icon otherwise.
export function worksiteCellHtml(row) {
  const change = row?.pending_change;
  if (!change) return resourceIconHtml(row.resource, row.worksite_type, null, row.terrain);
  const label = change.to_label || change.to || 'New worksite';
  const readyAt = change.stalled ? null : (change.ready_at || undefined);
  const now = Date.now();
  const until = readyAt ? Date.parse(readyAt) : NaN;
  const done = Number.isFinite(until) && until <= now;
  const icon = resourceIconHtml(change.to, label, NO_TIP, row.terrain);
  const eta = Number.isFinite(until) && !done ? `<span class="ws-build-eta" data-countdown-until="${esc(readyAt)}">${formatCountdown(until - now)}</span>` : '';
  if (!done) ensureWsBuildTicker();
  return `<span class="ws-build ${done ? 'is-done' : 'is-building'}" data-ws-build-label="${esc(label)}" data-ws-build-until="${esc(readyAt || '')}"${tipAttrs(buildTip(label, readyAt, now))}>${icon}<span class="ws-build-mark" aria-hidden="true">${done ? TICK_SVG : CLOCK_SVG}</span>${eta}</span>`;
}

// One timer for every build mark on the board: the tooltip's countdown ticks, and at zero the red clock
// becomes the green tick without a page reload. Stops itself once no building mark is on the page.
function tick() {
  const nodes = document.querySelectorAll('.ws-build.is-building[data-ws-build-until]');
  if (!nodes.length) { clearInterval(TIMER); TIMER = null; return; }
  const now = Date.now();
  nodes.forEach((node) => {
    const readyAt = node.dataset.wsBuildUntil;
    const until = Date.parse(readyAt);
    if (!Number.isFinite(until)) return;
    const label = node.dataset.wsBuildLabel || 'New worksite';
    const tip = buildTip(label, readyAt, now);
    node.setAttribute('data-tip', tip.title);
    if (tip.body) node.setAttribute('data-tip-body', tip.body); else node.removeAttribute('data-tip-body');
    node.setAttribute('aria-label', tip.body ? `${tip.title}. ${tip.body}` : tip.title);
    const eta = node.querySelector('.ws-build-eta');
    if (until <= now) {
      node.classList.remove('is-building'); node.classList.add('is-done');
      const mark = node.querySelector('.ws-build-mark'); if (mark) mark.innerHTML = TICK_SVG;
      if (eta) eta.remove();
      return;
    }
    const text = formatCountdown(until - now);
    if (eta && eta.textContent !== text) eta.textContent = text;
  });
}

export function ensureWsBuildTicker() {
  if (TIMER !== null || typeof document === 'undefined') return;
  TIMER = setInterval(tick, 1000);
}
