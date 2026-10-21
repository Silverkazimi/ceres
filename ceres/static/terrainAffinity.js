import { esc, terrainAffinityProfile } from './format.js';
import { splinterIconHtml, tooltipIconPath } from './icons.js';
import { tipAttrs, tipIcon } from './tooltip.js';

// item 5: "terrain bonus must be clearly using numbers ... Forrest terrain bonus
// should be Forrest terain bonus +10%". The number sits in the line itself -- this is a
// brief tooltip, and a brief tooltip never shows the key, which is why it read words-only.
function affinityName(peer, terrain) {
  const place = `${tipIcon(tooltipIconPath('terrain', terrain))}${terrain}`;
  if (peer.state === 'unknown') return { title: `${peer.splinter} splinter`, body: `${place} terrain modifier not reported` };
  if (peer.state === 'favoured') return { title: `${peer.splinter} splinter`, body: `${place} terrain bonus ${peer.display}` };
  if (peer.state === 'penalised') return { title: `${peer.splinter} splinter`, body: `${place} terrain penalty ${peer.display}` };
  return { title: `${peer.splinter} splinter`, body: `${place} terrain: no change (${peer.display})` };
}

function affinityBadge(peer, name, interactive = false, selectedSplinter = '', surface = '') {
  const knownValue = typeof peer.value === 'number' ? ` data-affinity-value="${esc(peer.value)}"` : '';
  if (interactive) {
    // the picker filter may hold several splinters, comma-separated.
    const selected = String(selectedSplinter).toLowerCase().split(',').includes(String(peer.splinter).toLowerCase());
    return `<button type="button" class="terrain-affinity-badge${selected ? ' is-selected' : ''}" data-picker-action="splinter" data-splinter="${esc(peer.splinter)}" data-affinity-state="${peer.state}"${knownValue} aria-label="Filter ${esc(peer.splinter)} cards" aria-pressed="${selected}"${tipAttrs({ ...name, icon: tooltipIconPath('splinter', peer.splinter), foot: selected ? 'Click again to remove this splinter from the filter.' : 'Click to add this splinter to the filter.', peer: 'picker_filters' })}>${splinterIconHtml(peer.splinter, false, { decorativeImage: true })}</button>`;
  }
  // the plot's own row ('editor-row') is a plain icon, not a tab stop
  // every other non-interactive surface (the mobile sheet's 'editor', the overlay) keeps
  // its existing tabindex="0" so its tooltip stays reachable by keyboard.
  const tabbable = surface === 'editor-row' ? '' : ' tabindex="0"';
  return `<span class="terrain-affinity-badge" data-splinter="${peer.splinter}" data-affinity-state="${peer.state}"${knownValue} role="img"${tabbable}${tipAttrs({ ...name, icon: tooltipIconPath('splinter', peer.splinter), peer: 'icons_identity' })}>${splinterIconHtml(peer.splinter, false, { decorativeImage: true })}</span>`;
}

export function terrainAffinityHtml(row, surface, selectedSplinter = '') {
  const profile = terrainAffinityProfile(row);
  const label = `${surface === 'picker' ? 'Worker terrain affinity' : 'Terrain affinity'} for ${profile.terrain}, plot ${row?.plot ?? row?.deed_uid ?? 'unknown'}`;
  const stateOrder = ['favoured', 'unchanged', 'unknown', 'penalised'];
  // the open plot's own row
  // ('editor-row') keeps the exact same horizontal-scroll shell and badge markup
  // the worker picker's filter bar ('picker') uses -- same classes, same sizing
  // but is never a clickable filter control, so only 'picker' is interactive.
  const interactive = surface === 'picker';
  const peers = [
    ...profile.peers.filter((peer) => peer.state === 'favoured'),
    ...(interactive ? [{ splinter: profile.neutral.splinter, value: 0, state: 'unchanged', display: profile.neutral.display }] : []),
    ...stateOrder.slice(1).flatMap((state) => profile.peers.filter((peer) => peer.state === state)),
  ]
    .map((peer) => affinityBadge(peer, affinityName(peer, profile.terrain), interactive, selectedSplinter, surface)).join('');
  const run = `<span class="terrain-affinity" data-terrain-affinity="${surface}" data-affinity-surface="${surface}" role="group" aria-label="${esc(label)}">${peers}</span>`;
  if (surface !== 'picker' && surface !== 'editor-row') return run;
  return `<span class="terrain-affinity-scroll"><span class="terrain-affinity-viewport">${run}</span><span class="terrain-affinity-edge is-start" aria-hidden="true" hidden></span><span class="terrain-affinity-edge is-end" aria-hidden="true" hidden></span></span>`;
}

export function bindTerrainAffinityOverflow(scope) {
  const cleanups = [];
  scope.querySelectorAll('.terrain-affinity-viewport').forEach((viewport) => {
    if (viewport._terrainAffinityCleanup) viewport._terrainAffinityCleanup();
    const shell = viewport.parentElement;
    const badges = [...viewport.querySelectorAll('.terrain-affinity-badge')];
    const start = shell.querySelector('.terrain-affinity-edge.is-start');
    const end = shell.querySelector('.terrain-affinity-edge.is-end');
    const update = () => {
      const width = viewport.clientWidth;
      const max = Math.max(0, viewport.scrollWidth - width);
      const overflowing = max > 1;
      if (overflowing) {
        viewport.tabIndex = 0;
        viewport.setAttribute('role', 'region');
        viewport.setAttribute('aria-label', 'Worker terrain affinity scroll area');
      } else {
        viewport.removeAttribute('tabindex');
        viewport.removeAttribute('role');
        viewport.removeAttribute('aria-label');
      }
      const visible = badges.map((badge) => ({ left: badge.offsetLeft - viewport.scrollLeft, right: badge.offsetLeft + badge.offsetWidth - viewport.scrollLeft }))
        .filter((badge) => badge.left >= -0.5 && badge.right <= width + 0.5);
      start.hidden = !overflowing || viewport.scrollLeft <= 1;
      end.hidden = !overflowing || viewport.scrollLeft >= max - 1;
      if (visible.length) {
        const rectW = end.getBoundingClientRect().width || start.getBoundingClientRect().width; // V (no page zoom)
        const edgeWidth = rectW || width * 0.03; // V (: no page zoom)
        // Use a visible inter-badge gap so an affordance never masks a settled state.
        const first = visible[0];
        const last = visible[visible.length - 1];
        start.style.left = `${first.left >= edgeWidth ? first.left - edgeWidth : first.right}px`;
        end.style.left = `${last.right + edgeWidth <= width ? last.right : last.left - edgeWidth}px`;
      }
    };
    const onKey = (event) => {
      if (!viewport.hasAttribute('tabindex')) return;
      const max = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
      let target;
      if (event.key === 'Home') target = 0;
      else if (event.key === 'End') target = max;
      else if (event.key === 'ArrowRight') {
        const next = badges.find((badge) => badge.offsetLeft > viewport.scrollLeft + 1);
        target = Math.min(max, next?.offsetLeft ?? max);
      } else if (event.key === 'ArrowLeft') {
        const previous = [...badges].reverse().find((badge) => badge.offsetLeft < viewport.scrollLeft - 1);
        target = Math.max(0, previous?.offsetLeft ?? 0);
      } else return;
      event.preventDefault();
      event.stopPropagation();
      viewport.scrollLeft = target;
      update();
    };
    viewport.addEventListener('scroll', update, { passive: true });
    viewport.addEventListener('keydown', onKey);
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null;
    resize?.observe(viewport);
    if (viewport.firstElementChild) resize?.observe(viewport.firstElementChild);
    const detach = typeof MutationObserver === 'function' ? new MutationObserver(() => {
      if (!viewport.isConnected) cleanup();
    }) : null;
    const cleanup = () => {
      resize?.disconnect();
      detach?.disconnect();
      viewport.removeEventListener('scroll', update);
      viewport.removeEventListener('keydown', onKey);
      delete viewport._terrainAffinityCleanup;
    };
    viewport._terrainAffinityCleanup = cleanup;
    if (viewport.ownerDocument?.body) detach?.observe(viewport.ownerDocument.body, { childList: true, subtree: true });
    update();
    cleanups.push(cleanup);
  });
  return () => cleanups.forEach((cleanup) => cleanup());
}
