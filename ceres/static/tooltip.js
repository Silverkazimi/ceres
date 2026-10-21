// esc from escape.js, not format.js (whose imports reach the whole board graph), so /cards can run this engine.
import { esc } from './escape.js';
import { ASSET_BASE } from './state.js';

const PEER_DEPTHS = Object.freeze({
  "icons_identity": "brief",
  "resource_identity": "brief",
  "icons_boosts": "extended",
  "territory_identity": "extended",
  "bloodline_plot_badges": "extended",
  "ability_icons": "extended",
  "plot_warnings": "extended",
  "plot_metrics": "extended",
  "plot_properties": "extended",
  "plot_pending": "extended",
  "data_disagreements": "extended",
  "dec_efficiency": "extended",
  "candidate_combine": "extended",
  "candidate_row": "extended",
  "bulk_clear_worker": "extended",
  "bulk_fill_worker": "extended",
  "editor_change_metrics": "extended",
  "candidate_cooldown": "extended",
  "candidate_terrain": "extended",
  "candidate_metric": "extended",
  "harvest_per_hour": "extended",
  "candidate_bloodline": "extended",
  "picker_filters": "extended",
  "picker_hidden": "extended",
  "checkout_metrics": "extended",
  "checkout_blockers": "extended",
  "worksite_controls": "extended",
  "worksite_needs": "extended",
  "worksite_rate": "extended",
  "worksite_details": "extended",
  "board_controls": "extended",
  "tile_occupants": "extended",
  "tile_empty_targets": "extended",
  "tile_actions": "extended",
  "tile_warnings": "extended",
  "tile_power": "extended",
  "tile_base_pp": "extended",
  "tile_ability_icons": "extended",
  "tile_ability_overflow": "extended",
  "tile_chip_rail": "extended",
  "tile_art_fallback": "brief"
});

const ICON_PATH = /^[a-z0-9/_-]+\.(?:svg|png|webp)$/i;
// the Dark Energy (DEC) icon, a committed UI icon by his named exception
// to golden rule 1 (his shrunk copy of the Splinterlands image). Served locally.
export const DARK_ENERGY_ICON = 'land/dark-energy.png';
export const DARK_ENERGY_SRC = `${ASSET_BASE}/${DARK_ENERGY_ICON}`;
// Remote icon addresses allowed through the tooltip renderer; none are needed now.
const REMOTE_ICONS = new Set();

function isIcon(path) {
  return REMOTE_ICONS.has(String(path || '')) || ICON_PATH.test(String(path || ''));
}

function iconSrc(path) {
  return REMOTE_ICONS.has(path) ? path : `${ASSET_BASE}/${path}`;
}
const SEVERITIES = new Set(['info', 'caution', 'danger', 'success', 'neutral']);
let TOOLTIP_EL = null;
let TOOLTIP_INNER_EL = null;
let TOOLTIP_TARGET = null;
let TOOLTIP_OPEN_TIMER = null;
let TOOLTIP_CLOSE_TIMER = null;
let TOOLTIP_FOCUS_TARGET = null;
let TOUCH_POINTER = null;
let TOUCH_OPENED = false; // the open tooltip was raised by a touch long-press: only then does its box catch taps (dismiss)
let TOUCH_LONG_PRESS_TIMER = null;
let TOUCH_SUPPRESS_UNTIL = 0;
// (maintainer: "there iss abug with thetooltip of the wallet appearing without
// hover, especially when changing tabs and then returning to the browser but also
// while surfing elsewhere"): the wallet edge control keeps DOM focus after its drawer
// closes; when the browser tab/window regains focus, that element is re-focused by
// the platform, `focusin` fires again with no real user action behind it, and the
// tooltip pops with no hover. Tracks the last time the WINDOW itself (not an
// element) regained focus/visibility, so a `focusin` landing immediately after can be
// told apart from one the user actually caused by tabbing/clicking.
let WINDOW_REFOCUSED_AT = 0;
// (maintainer, 2026-09-29: tooltips create hurdles when scrolling): after any scroll or wheel turn no tooltip opens
// until the pointer really moves (more than SCROLL_QUIET_PX) and rests again -- content sliding under a still pointer
// is not intent. A wheel turn closes an open tooltip and scrolls what is underneath it.
let SCROLL_QUIET = false;
let SCROLL_QUIET_AT = null;
const SCROLL_QUIET_PX = 4;
const WINDOW_REFOCUS_IGNORE_MS = 400;
const TOUCH_LONG_PRESS_MS = 500;
const TOUCH_MOVE_TOLERANCE = 10;

// Custom tooltip (board-polish batch 9 continued, item 11). the owner: "the
// tooltips are hidden behind the mouse and should use a beautiful tooltip
// style above the cursor tip." Native `title` renders below-right of the
// pointer (so the pointer covers it) and can't be styled at all -- every
// former `title="..."` in this file was converted to `tipAttrs()` (see that
// helper's own comment), which emits `data-tip` (read here) plus `aria-label`
// (the non-hover accessible fallback the tooltip requirement asks for).
// One shared tooltip element, ONE set of document-level delegated listeners
// (mouse, focus, and touch-pointer intent) rather than a per-element binding
// the board re-renders its whole tbody on every filter/sort/refresh
// (render()), so per-element listeners would need re-wiring on every
// re-render; delegation on `document` needs none. `position:fixed` and
// appended once to <body> outside the table -- it can never add row height
// or shift layout, since it isn't part of the table's own flow.
// (`TOOLTIP_EL`/`TOOLTIP_TARGET` are declared at the top of this file for the
// shared delegated handlers.)
export function initTooltips() {
  TOOLTIP_EL = document.getElementById('ceres-tooltip');
  if (!TOOLTIP_EL) {
    TOOLTIP_EL = document.createElement('div');
    TOOLTIP_EL.id = 'ceres-tooltip';
    TOOLTIP_EL.setAttribute('role', 'tooltip');
    TOOLTIP_EL.hidden = true;
    document.body.appendChild(TOOLTIP_EL);
  }
  TOOLTIP_INNER_EL = TOOLTIP_EL.querySelector('.tip-inner');
  if (!TOOLTIP_INNER_EL) {
    TOOLTIP_INNER_EL = document.createElement('div');
    TOOLTIP_INNER_EL.className = 'tip-inner';
    TOOLTIP_EL.appendChild(TOOLTIP_INNER_EL);
  }
  if (TOOLTIP_EL.dataset.listenersAttached === '1') return;
  TOOLTIP_EL.dataset.listenersAttached = '1';

  // nothing in the badge column or its
  // slide-out shows a tooltip; the badges keep their aria labels. The flag (column header) is not a badge.
  const NO_TIP_ZONE = '#badge-bar, .badge-overlay';
  const findTrigger = (event) => {
    const trigger = event.target.closest?.('[data-tip]');
    return trigger && !trigger.closest(NO_TIP_ZONE) ? trigger : null;
  };

  document.addEventListener('mouseover', (event) => {
    if (event.sourceCapabilities?.firesTouchEvents || Date.now() < TOUCH_SUPPRESS_UNTIL) return;
    if (SCROLL_QUIET) return;  // wait for a real pointer move after scrolling
    const trigger = findTrigger(event);
    if (trigger && !(event.relatedTarget && trigger.contains(event.relatedTarget))) { TOUCH_OPENED = false; showTooltip(trigger); }
  });
  document.addEventListener('mouseout', (event) => {
    const trigger = findTrigger(event);
    if (trigger && trigger === TOOLTIP_PENDING && !(event.relatedTarget && trigger.contains(event.relatedTarget))) {
      clearTimeout(TOOLTIP_OPEN_TIMER); // passed over, never rested on -- no tooltip
      TOOLTIP_OPEN_TIMER = null;
      TOOLTIP_PENDING = null;
    }
    if (!trigger || trigger !== TOOLTIP_TARGET) return;
    if (event.relatedTarget && (trigger.contains(event.relatedTarget) || (TOUCH_OPENED && TOOLTIP_EL.contains(event.relatedTarget)))) return;
    scheduleHideTooltip(); // leaving the area that raised it closes it at once, whatever is next
  });
  document.addEventListener('focusin', (event) => {
    if (Date.now() < TOUCH_SUPPRESS_UNTIL) return;
    // ignore a focusin that lands immediately after the WINDOW itself (not
    // an element) regained focus/visibility -- that is the platform re-focusing
    // whatever element last held focus, not a real user interaction.
    if (Date.now() - WINDOW_REFOCUSED_AT < WINDOW_REFOCUS_IGNORE_MS) return;
    const trigger = findTrigger(event);
    if (!trigger) return;
    // only KEYBOARD focus (tabbing) should pop a tooltip on its own
    // mouse/pointer focus is already covered by the `mouseover` handler above, and a
    // programmatic/platform refocus (window regaining focus, a drawer closing and
    // returning focus to its trigger) should not show one uninvited. `:focus-visible`
    // is wrapped in try/catch for older engines that don't support it as a `matches()`
    // argument -- falling back to the old always-show behaviour there, since those
    // engines are a smaller, already-accepted risk than regressing every browser.
    try {
      if (!trigger.matches(':focus-visible')) return;
    } catch (_) { /* focus-visible unsupported -- fall through and show, as before */ }
    TOOLTIP_FOCUS_TARGET = trigger;
    TOUCH_OPENED = false;
    showTooltip(trigger, { immediate: true });
  });
  document.addEventListener('focusout', (event) => {
    const trigger = findTrigger(event);
    if (trigger && trigger === TOOLTIP_TARGET && !(event.relatedTarget && trigger.contains(event.relatedTarget))) {
      scheduleHideTooltip();
    }
  });
  document.addEventListener('pointerdown', (event) => { if (event.pointerType === 'mouse' && !TOOLTIP_EL.hidden) hideTooltip(); }, true); // a click on or beside the trigger ends its tooltip
  TOOLTIP_EL.addEventListener('pointerenter', cancelHideTooltip);
  TOOLTIP_EL.addEventListener('pointerleave', scheduleHideTooltip);
  // a tab/window refocus fires `focusin` on whatever element last held
  // focus (the wallet edge toggle, most often) with no real user action behind it
  // `focusin` above ignores anything landing within WINDOW_REFOCUS_IGNORE_MS of
  // either of these two signals firing.
  window.addEventListener('focus', () => { WINDOW_REFOCUSED_AT = Date.now(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') { WINDOW_REFOCUSED_AT = Date.now(); return; }
    // Tab/window is being hidden -- a tooltip left open (or about to open) has no
    // hover left to keep it honest once the user is looking elsewhere.
    if (TOOLTIP_OPEN_TIMER) { clearTimeout(TOOLTIP_OPEN_TIMER); TOOLTIP_OPEN_TIMER = null; TOOLTIP_PENDING = null; }
    hideTooltip();
  });
  // Losing WINDOW focus entirely (switching apps, not just tabs) gets the same
  // immediate-hide treatment as `visibilitychange` -- a tooltip should never survive
  // the user leaving the browser altogether.
  window.addEventListener('blur', () => {
    if (TOOLTIP_OPEN_TIMER) { clearTimeout(TOOLTIP_OPEN_TIMER); TOOLTIP_OPEN_TIMER = null; TOOLTIP_PENDING = null; }
    hideTooltip();
  });
  const enterScrollQuiet = () => {
    SCROLL_QUIET = true;
    SCROLL_QUIET_AT = null;
    if (TOOLTIP_OPEN_TIMER) { clearTimeout(TOOLTIP_OPEN_TIMER); TOOLTIP_OPEN_TIMER = null; TOOLTIP_PENDING = null; }
  };
  window.addEventListener('scroll', (event) => {
    if (event.target instanceof Node && TOOLTIP_EL.contains(event.target)) return;
    enterScrollQuiet();
    hideTooltip();
  }, true);
  window.addEventListener('wheel', () => { enterScrollQuiet(); if (!TOOLTIP_EL.hidden) hideTooltip(); }, { capture: true, passive: true });
  TOOLTIP_EL.addEventListener('wheel', (event) => {
    event.preventDefault();
    TOOLTIP_EL.style.pointerEvents = 'none';
    let box = document.elementFromPoint(event.clientX, event.clientY); // V (no page zoom)
    while (box && box !== document.body && box !== document.documentElement) {
      const style = getComputedStyle(box);
      if (/(auto|scroll)/.test(style.overflowY + style.overflowX) && (box.scrollHeight > box.clientHeight || box.scrollWidth > box.clientWidth)) break;
      box = box.parentElement;
    }
    const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1; // V (no page zoom)
    (box && box !== document.body && box !== document.documentElement ? box : document.scrollingElement || document.documentElement)
      .scrollBy({ left: event.deltaX * scale, top: event.deltaY * scale });
    enterScrollQuiet();
    hideTooltip();
  }, { passive: false });
  document.addEventListener('mousemove', (event) => {
    if (!SCROLL_QUIET) return;
    if (!SCROLL_QUIET_AT) { SCROLL_QUIET_AT = { x: event.clientX, y: event.clientY }; return; } // V (no page zoom)
    if (Math.hypot(event.clientX - SCROLL_QUIET_AT.x, event.clientY - SCROLL_QUIET_AT.y) <= SCROLL_QUIET_PX) return; // V (no page zoom)
    SCROLL_QUIET = false;
    SCROLL_QUIET_AT = null;
    const under = document.elementFromPoint(event.clientX, event.clientY)?.closest?.('[data-tip]'); // V (no page zoom)
    if (under && !under.closest(NO_TIP_ZONE)) { TOUCH_OPENED = false; showTooltip(under); }
  }, { passive: true });
  const reposition = () => {
    if (!TOOLTIP_TARGET || TOOLTIP_EL.hidden) return;
    if (!TOOLTIP_TARGET.isConnected) { hideTooltip(); return; }
    positionTooltip(TOOLTIP_TARGET);
  };
  window.addEventListener('resize', reposition);
  window.visualViewport?.addEventListener('resize', reposition);
  window.visualViewport?.addEventListener('scroll', reposition);
  new ResizeObserver(reposition).observe(TOOLTIP_INNER_EL);
  document.addEventListener('click', (event) => {
    const trigger = findTrigger(event);
    const touch = event.sourceCapabilities?.firesTouchEvents || Date.now() < TOUCH_SUPPRESS_UNTIL;
    if (touch) {
      const held = Boolean(trigger && TOUCH_POINTER?.longPressed && TOUCH_POINTER.trigger === trigger);
      if (held) {
        event.preventDefault();
        event.stopImmediatePropagation();
        TOUCH_POINTER = null;
        return;
      }
      hideTooltip();
      return;
    }
    if (!trigger && !TOOLTIP_EL.contains(event.target)) hideTooltip();
  }, true);
  document.addEventListener('pointerdown', (event) => {
    if (event.pointerType !== 'touch') return;
    TOUCH_SUPPRESS_UNTIL = Date.now() + TOUCH_LONG_PRESS_MS + 800;
    if (TOUCH_LONG_PRESS_TIMER) clearTimeout(TOUCH_LONG_PRESS_TIMER);
    TOUCH_LONG_PRESS_TIMER = null;
    TOUCH_POINTER = null;
    hideTooltip();
    const trigger = findTrigger(event);
    if (!trigger?.closest?.('.mobile-plot-sheet') || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return; // V (no page zoom)
    TOUCH_POINTER = { id: event.pointerId, trigger, x: event.clientX, y: event.clientY, longPressed: false }; // V (no page zoom)
    TOUCH_LONG_PRESS_TIMER = setTimeout(() => {
      TOUCH_LONG_PRESS_TIMER = null;
      if (!TOUCH_POINTER || TOUCH_POINTER.id !== event.pointerId || !trigger.isConnected) return;
      TOUCH_POINTER.longPressed = true;
      TOUCH_OPENED = true;
      showTooltip(trigger, { immediate: true });
    }, TOUCH_LONG_PRESS_MS);
  }, true);
  document.addEventListener('pointermove', (event) => {
    if (!TOUCH_POINTER || event.pointerId !== TOUCH_POINTER.id || TOUCH_POINTER.longPressed) return;
    if (!Number.isFinite(event.clientX) || !Number.isFinite(event.clientY) // V (no page zoom)
        || Math.hypot(event.clientX - TOUCH_POINTER.x, event.clientY - TOUCH_POINTER.y) > TOUCH_MOVE_TOLERANCE) { // V (no page zoom)
      clearTimeout(TOUCH_LONG_PRESS_TIMER);
      TOUCH_LONG_PRESS_TIMER = null;
      TOUCH_POINTER = null;
    }
  }, true);
  const finishTouch = (event) => {
    if (!TOUCH_POINTER || event.pointerId !== TOUCH_POINTER.id) return;
    if (TOUCH_LONG_PRESS_TIMER) clearTimeout(TOUCH_LONG_PRESS_TIMER);
    TOUCH_LONG_PRESS_TIMER = null;
    TOUCH_SUPPRESS_UNTIL = Date.now() + 800;
    if (event.type === 'pointercancel' || !TOUCH_POINTER.longPressed) TOUCH_POINTER = null;
  };
  document.addEventListener('pointerup', finishTouch, true);
  document.addEventListener('pointercancel', finishTouch, true);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && TOOLTIP_EL && !TOOLTIP_EL.hidden) {
      hideTooltip(true);
      event.stopPropagation();
    }
  });
}

// (maintainer: "add a tooltip animation with a small delay ... the main reason is to avoid tons of tooltips popping
// up when moving the cursor over the board"): a tooltip opens only once the cursor RESTS on something (intent delay).
// every tooltip waits the same
// delay, even with another one open, and closes at once. Keyboard focus opens at once. A touch tap never opens a
// tooltip; mobile plot tiles require the 500ms stationary hold installed in initTooltips().
const TOOLTIP_INTENT_MS = 350;
let TOOLTIP_PENDING = null;

// when a modal closes, the element left under the stationary pointer gets a synthetic mouseover and its
// tooltip opens -- and stays, since the pointer never entered it. A closing modal calls this: the open tooltip goes
// and none opens until the pointer really moves.
let TOOLTIP_HELD = false;
export function holdTooltipsUntilPointerMoves() {
  TOOLTIP_HELD = true;
  if (TOOLTIP_OPEN_TIMER) { clearTimeout(TOOLTIP_OPEN_TIMER); TOOLTIP_OPEN_TIMER = null; TOOLTIP_PENDING = null; }
  hideTooltip();
  document.addEventListener('mousemove', () => { TOOLTIP_HELD = false; }, { once: true, capture: true });
}

function showTooltip(target, { immediate = false } = {}) {
  if (!target.getAttribute('data-tip') || !TOOLTIP_EL) return;
  if (TOOLTIP_HELD && !immediate) return;
  cancelHideTooltip();
  if (TOOLTIP_OPEN_TIMER) clearTimeout(TOOLTIP_OPEN_TIMER);
  TOOLTIP_PENDING = target;
  TOOLTIP_OPEN_TIMER = setTimeout(() => {
    TOOLTIP_OPEN_TIMER = null;
    TOOLTIP_PENDING = null;
    renderTooltip(target);
  }, immediate ? 0 : TOOLTIP_INTENT_MS);
}

function renderTooltip(target) {
  if (TOOLTIP_EL) { TOOLTIP_EL.style.pointerEvents = ''; TOOLTIP_EL.classList.toggle('is-touch', TOUCH_OPENED); }  // re-armed after a wheel pass-through
  const title = target.getAttribute('data-tip');
  if (!title || !TOOLTIP_EL || !TOOLTIP_INNER_EL) return;
  const peer = target.getAttribute('data-tip-peer') || '';
  const depth = PEER_DEPTHS[peer] || target.getAttribute('data-tip-depth') || 'extended';
  if (peer && PEER_DEPTHS[peer] !== target.getAttribute('data-tip-depth')) return;
  TOOLTIP_TARGET = target;
  const key = target.getAttribute('data-tip-key') || '';
  const body = target.getAttribute('data-tip-body') || '';
  const foot = target.getAttribute('data-tip-foot') || '';
  const kind = target.getAttribute('data-tip-kind') || 'explain';
  const severity = target.getAttribute('data-tip-severity');
  TOOLTIP_EL.dataset.peer = peer;
  TOOLTIP_EL.dataset.depth = depth;
  TOOLTIP_EL.dataset.kind = kind;
  const workerLayout = target.getAttribute('data-tip-layout') === 'worker';
  if (workerLayout) TOOLTIP_EL.dataset.layout = 'worker';
  else delete TOOLTIP_EL.dataset.layout;
  if (severity) TOOLTIP_EL.dataset.severity = severity;
  else delete TOOLTIP_EL.dataset.severity;
  TOOLTIP_INNER_EL.replaceChildren();

  const head = document.createElement('div');
  head.className = 'tip-head';
  const headIcon = target.getAttribute('data-tip-icon');
  appendIcon(head, headIcon, 'tip-icon');
  const titleEl = document.createElement('span');
  titleEl.className = 'tip-title';
  appendRich(titleEl, title);
  head.append(titleEl);
  if (workerLayout) {
    for (const attribute of ['data-tip-identity', 'data-tip-level']) {
      const text = target.getAttribute(attribute);
      if (!text) continue;
      const line = document.createElement('div');
      line.className = attribute === 'data-tip-identity' ? 'tip-identity' : 'tip-level';
      appendRich(line, text);
      head.append(line);
    }
  }
  const bodyEl = document.createElement('div');
  bodyEl.className = 'tip-body';
  const items = parseItems(target.getAttribute('data-tip-items'));
  if (depth === 'extended' && key) {
    const keyEl = document.createElement('div');
    keyEl.className = 'tip-key';
    appendRich(keyEl, key);
    bodyEl.append(keyEl);
  }
  if (items) appendItems(bodyEl, items, { title: plainText(title), headIcon: ICON_PATH.test(headIcon || '') ? headIcon : null });
  else if (body) appendRich(bodyEl, body);
  const footEl = document.createElement('div');
  footEl.className = 'tip-foot';
  if (depth === 'extended' && foot) appendRich(footEl, foot);
  TOOLTIP_INNER_EL.append(head, bodyEl, footEl);
  const appearing = TOOLTIP_EL.hidden; // animate in from hidden; a tooltip already open just moves
  TOOLTIP_EL.hidden = false;
  positionTooltip(target);
  if (appearing) {
    TOOLTIP_EL.classList.remove('is-visible');
    void TOOLTIP_EL.offsetWidth; // start the transition from the hidden pose, not from display:none
  }
  TOOLTIP_EL.classList.add('is-visible');
}

function appendIcon(parent, icon, className) {
  if (isIcon(icon)) {
    const image = document.createElement('img');
    image.className = className;
    image.alt = '';
    image.src = iconSrc(icon);
    parent.append(image);
    return;
  }
  const dot = document.createElement('span');
  dot.className = 'tip-dot';
  parent.append(dot);
}

// his words: "Every terrain, splinter, ability, rsource, rarity, type which has a
// clear icon, must be used with the icon for total clarity" -- including a name inside a
// sentence. A caller marks an inline icon with tipIcon(path); the renderer swaps the marker
// for an <img>, and every plain-text surface (aria-label, data-tip-body) strips it.
const INLINE_ICON = /\{\{ic:([^}]+)\}\}/g;

export function tipIcon(path) {
  return path && ICON_PATH.test(String(path)) ? `{{ic:${path}}}` : '';
}

export function plainText(text) {
  return String(text ?? '').replace(INLINE_ICON, '').replace(/[ \t]{2,}/g, ' ').trim();
}

export function appendRich(parent, text) {
  const source = String(text ?? '');
  let last = 0;
  for (const match of source.matchAll(INLINE_ICON)) {
    if (match.index > last) parent.append(document.createTextNode(source.slice(last, match.index)));
    if (isIcon(match[1])) {
      const image = document.createElement('img');
      image.className = 'tip-inline-icon';
      image.alt = '';
      image.src = iconSrc(match[1]);
      parent.append(image);
    }
    last = match.index + match[0].length;
  }
  if (last < source.length) parent.append(document.createTextNode(source.slice(last)));
}

function parseItems(serialized) {
  if (!serialized) return null;
  try {
    const items = JSON.parse(serialized);
    return normalizeItems(items);
  } catch {
    return null;
  }
}

function words(text) {
  return ` ${plainText(text).toLowerCase().replace(/[^a-z0-9%+.,-]+/g, ' ').trim()} `;
}

// items 2 and 3. Every row shares ONE grid (icon | label | value), so labels and
// values line up vertically across the whole tooltip. An icon identical to the one just
// above it (or to the title's) is not repeated, and a label the title already says
// ("Bloodline: Elf" over "Elf: ...") is dropped -- the row becomes a plain line instead.
export function appendItems(parent, items, context = {}) {
  const list = document.createElement('div');
  list.className = 'tip-items';
  const titleWords = words(context.title);
  let group = '';
  let previousIcon = context.headIcon || null;
  for (const item of items) {
    if (item.group && item.group !== group) {
      group = item.group;
      const heading = document.createElement('div');
      heading.className = 'tip-group';
      heading.textContent = group;
      list.append(heading);
      previousIcon = null;
    }
    const cell = document.createElement('span');
    cell.className = 'tip-item-icon-cell';
    if (item.icon && item.icon !== previousIcon) appendIcon(cell, item.icon, 'tip-item-icon');
    previousIcon = item.icon || null;
    list.append(cell);
    const label = item.name && !titleWords.includes(words(item.name)) ? item.name : '';
    if (label) {
      const name = document.createElement('span');
      name.className = item.nowrap ? 'tip-item-name tip-item-name--nowrap' : 'tip-item-name';
      if (item.labelIcon) {
        name.classList.add('has-label-icon');
        const text = document.createElement('span');
        appendRich(text, label);
        name.append(text);
        appendIcon(name, item.labelIcon, 'tip-label-icon');
      } else appendRich(name, label);
      list.append(name);
    }
    const effect = document.createElement('span');
    effect.className = label ? 'tip-item-effect' : 'tip-item-effect is-line';
    appendRich(effect, item.effect);
    list.append(effect);
  }
  parent.append(list);
}

function hideTooltip(restoreFocus = false) {
  if (!TOOLTIP_EL) return;
  if (TOOLTIP_OPEN_TIMER) clearTimeout(TOOLTIP_OPEN_TIMER);
  if (TOOLTIP_CLOSE_TIMER) clearTimeout(TOOLTIP_CLOSE_TIMER);
  TOOLTIP_OPEN_TIMER = null;
  TOOLTIP_CLOSE_TIMER = null;
  const focusTarget = TOOLTIP_FOCUS_TARGET;
  TOOLTIP_PENDING = null;
  TOOLTIP_TARGET = null;
  TOOLTIP_FOCUS_TARGET = null;
  TOOLTIP_EL.classList.remove('is-visible');
  TOOLTIP_EL.hidden = true;
  if (restoreFocus && document.activeElement !== focusTarget) {
    const restoreTarget = focusTarget?.isConnected
      ? focusTarget
      : document.querySelector('[data-tip][tabindex="0"], button[data-tip], [data-tip]:not([disabled])');
    restoreTarget?.focus({ preventScroll: true });
  }
}

function cancelHideTooltip() {
  if (TOOLTIP_CLOSE_TIMER) clearTimeout(TOOLTIP_CLOSE_TIMER);
  TOOLTIP_CLOSE_TIMER = null;
}

function scheduleHideTooltip() {
  if (TOOLTIP_OPEN_TIMER) clearTimeout(TOOLTIP_OPEN_TIMER);
  TOOLTIP_OPEN_TIMER = null;
  if (!TOOLTIP_EL || TOOLTIP_EL.hidden) return;
  hideTooltip(); // closing is instant
}

// Above the cursor with a small offset and an arrow (the owner's ask), but
// FLIPPED to below when there isn't room above (rows near the top of the
// viewport, e.g. the sticky header row) and CLAMPED horizontally so it never
// renders off-screen at either edge -- both measured against the real
// viewport/tooltip size via getBoundingClientRect(), not assumed.
export function calculateTooltipPosition(trigger, tooltip, viewport, borders = { left: 0, right: 0, top: 0, bottom: 0 }, margin = 8, gap = 10, options = {}) {
  const minLeft = (viewport.left || 0) + margin;
  const minTop = (viewport.top || 0) + margin;
  const maxLeft = Math.max(minLeft, (viewport.left || 0) + viewport.width - tooltip.width - margin);
  const viewportRight = (viewport.left || 0) + viewport.width;
  const viewportBottom = (viewport.top || 0) + viewport.height;
  const triggerRight = trigger.right ?? trigger.left + trigger.width;
  const triggerBottom = trigger.bottom ?? trigger.top + trigger.height;
  const triggerCenterY = trigger.top + trigger.height / 2;
  const sideMargin = 8;
  const sideGap = 8;
  const sideEnabled = options.side === true && viewport.width > 1279;
  if (sideEnabled) {
    const right = triggerRight + sideGap + tooltip.width <= viewportRight - sideMargin;
    const left = trigger.left - sideGap - tooltip.width >= (viewport.left || 0) + sideMargin;
    if (right || left) {
      const side = right ? 'right' : 'left';
      const sideLeft = right ? triggerRight + sideGap : trigger.left - sideGap - tooltip.width;
      const sideMinTop = (viewport.top || 0) + sideMargin;
      const sideMaxTop = Math.max(sideMinTop, viewportBottom - tooltip.height - sideMargin);
      const top = Math.max(sideMinTop, Math.min(triggerCenterY - tooltip.height / 2, sideMaxTop));
      const borderTop = borders.top || 0;
      const borderBottom = borders.bottom || 0;
      const maxArrowTop = Math.max(10, tooltip.height - borderTop - borderBottom - 10);
      const arrowTop = Math.max(10, Math.min(triggerCenterY - top - borderTop, maxArrowTop));
      return { left: sideLeft, top, below: false, side, arrowLeft: null, arrowTop };
    }
  }
  const unclampedLeft = trigger.left + trigger.width / 2 - tooltip.width / 2;
  const left = Math.max(minLeft, Math.min(unclampedLeft, maxLeft));
  let top = trigger.top - tooltip.height - gap;
  let below = false;
  if (top < minTop) {
    top = triggerBottom + gap;
    below = true;
  }
  const maxTop = Math.max(minTop, (viewport.top || 0) + viewport.height - tooltip.height - margin);
  top = Math.max(minTop, Math.min(top, maxTop));
  const maxArrow = tooltip.width - borders.left - borders.right - 10;
  const arrowLeft = Math.max(10, Math.min(trigger.left + trigger.width / 2 - left - borders.left, maxArrow));
  return { left, top, below, side: null, arrowLeft, arrowTop: null };
}

// (4): a `display: contents` trigger (the Fill/Changes strip's clickable slot wrapper) has a zero rect
// the tooltip anchors to its first laid-out descendant instead of the top-left corner of the screen.
function anchorRect(target) {
  let el = target;
  for (let i = 0; el && i < 4; i++) {
    const rect = el.getBoundingClientRect(); // V (no page zoom)
    if (rect.width || rect.height) return rect;
    el = el.firstElementChild;
  }
  return target.getBoundingClientRect(); // V (no page zoom)
}

function positionTooltip(target) {
  const visual = window.visualViewport;
  const viewport = { left: visual?.offsetLeft || 0, top: visual?.offsetTop || 0,
    width: visual?.width || document.documentElement.clientWidth,
    height: visual?.height || window.innerHeight }; // V (no page zoom)
  const margin = 12;
  TOOLTIP_EL.style.setProperty('--tooltip-available-width', `${Math.max(0, viewport.width - margin * 2)}px`);
  const computed = getComputedStyle(TOOLTIP_EL);
  const borderHeight = (parseFloat(computed.borderTopWidth) || 0) + (parseFloat(computed.borderBottomWidth) || 0);
  TOOLTIP_EL.style.setProperty('--tooltip-available-height', `${Math.max(0, viewport.height - margin * 2 - borderHeight)}px`);
  const workerSide = target.getAttribute('data-tip-layout') === 'worker';
  const result = calculateTooltipPosition(
    anchorRect(target),
    { width: TOOLTIP_EL.offsetWidth, height: TOOLTIP_EL.offsetHeight }, // Measure final size, not the entrance animation scale.
    viewport,
    {
      left: parseFloat(computed.borderLeftWidth) || 0,
      right: parseFloat(computed.borderRightWidth) || 0,
      top: parseFloat(computed.borderTopWidth) || 0,
      bottom: parseFloat(computed.borderBottomWidth) || 0,
    },
    margin,
    10,
    { side: workerSide },
  );
  TOOLTIP_EL.style.left = `${result.left}px`; // V (: no page zoom)
  TOOLTIP_EL.style.top = `${result.top}px`;
  TOOLTIP_EL.classList.toggle('is-below', result.below);
  TOOLTIP_EL.classList.toggle('is-side-right', result.side === 'right');
  TOOLTIP_EL.classList.toggle('is-side-left', result.side === 'left');
  TOOLTIP_EL.style.setProperty('--ceres-tooltip-arrow-left', result.arrowLeft === null ? '' : `${result.arrowLeft}px`);
  TOOLTIP_EL.style.setProperty('--ceres-tooltip-arrow-top', result.arrowTop === null ? '' : `${result.arrowTop}px`);
}

// Custom-tooltip attribute pair (board-polish batch 9 continued, item 11
// the owner: "the tooltips are hidden behind the mouse and should use a
// beautiful tooltip style above the cursor tip"). Every former `title="..."`
// call site in this file was converted to `tipAttrs(...)`, which emits BOTH
// `data-tip` (read by initTooltips() below to drive the styled hover/focus
// tooltip) AND `aria-label` (the required non-hover fallback -- an assistive
// technology reading the element gets the same text without needing hover or
// the custom JS at all). Never emits a native `title` -- the browser's own
// below-the-cursor tooltip would otherwise double up with the custom one.
// Optional `severity` (the same vocabulary as ceres/notices.py's `severity`
// field: info/caution/danger/success/neutral) adds a `data-tip-severity`
// attribute, read by showTooltip() to accent #ceres-tooltip's border
// background via CSS. Omitted (or an unrecognised value) renders the
// neutral default -- no CSS rule matches it, so it's a safe no-op rather
// than a broken tooltip. Never part of `aria-label`; severity is a colour
// cue for sighted hover/focus only, not part of the accessible text.
export function tipAttrs(model, severity) {
  if (model === null || model === undefined || model === '') return '';
  const legacy = typeof model !== 'object';
  // a caller that passes a MODEL OBJECT as `title` (tipAttrs({ title: model }))
  // used to render the literal string "[object Object]" on the surface. A non-string
  // title is always a caller bug, so refuse the tooltip instead of showing that to him.
  const rawTitle = legacy ? model : model.title;
  const titleOk = typeof rawTitle === 'string' || typeof rawTitle === 'number';
  const title = titleOk ? String(rawTitle) : '';
  if (!title) return '';
  const key = legacy ? '' : String(model.key ?? '');
  const suppliedBody = legacy ? '' : (Array.isArray(model.body) ? model.body.join('\n') : String(model.body ?? ''));
  const foot = legacy ? '' : String(model.foot ?? '');
  const peer = legacy ? '' : String(model.peer ?? '');
  const declaredDepth = peer ? PEER_DEPTHS[peer] : '';
  if (peer && (!declaredDepth || (model.depth && model.depth !== declaredDepth))) return '';
  const depth = peer ? declaredDepth : '';
  const kind = legacy ? 'explain' : (model.kind || 'explain');
  const items = legacy ? null : normalizeItems(model.items);
  const body = items ? items.map((item) => (item.name ? `${item.name}: ${item.effect}` : item.effect)).join('\n') : suppliedBody;
  const identity = !legacy && model.layout === 'worker' && typeof model.identity === 'string' ? model.identity : '';
  const level = !legacy && model.layout === 'worker' && typeof model.level === 'string' ? model.level : '';
  const accessibleText = [title, identity, level, key, body, foot].map(plainText).filter(Boolean).join('. ');
  const rawSeverity = legacy ? severity : (model.severity || severity);
  const sev = SEVERITIES.has(rawSeverity) ? rawSeverity : '';
  const attrs = [` data-tip="${esc(title)}"`, ` data-tip-kind="${esc(kind)}"`, ` aria-label="${esc(accessibleText)}"`];
  if (!legacy && model.layout === 'worker') {
    attrs.push(' data-tip-layout="worker"');
    if (identity) attrs.push(` data-tip-identity="${esc(identity)}"`);
    if (level) attrs.push(` data-tip-level="${esc(level)}"`);
  }
  if (peer) attrs.push(` data-tip-peer="${esc(peer)}"`, ` data-tip-depth="${depth}"`);
  if (body) attrs.push(` data-tip-body="${esc(body)}"`);
  if (foot) attrs.push(` data-tip-foot="${esc(foot)}"`);
  if (key) attrs.push(` data-tip-key="${esc(key)}"`);
  if (!legacy && ICON_PATH.test(model.icon || '')) attrs.push(` data-tip-icon="${esc(model.icon)}"`);
  if (items) attrs.push(` data-tip-items="${esc(JSON.stringify(items))}"`);
  if (sev) attrs.push(` data-tip-severity="${esc(sev)}"`);
  return attrs.join('');
}

function normalizeItems(items) {
  if (!Array.isArray(items) || !items.length) return null;
  const normalized = items.map((item) => ({
    icon: item?.icon && ICON_PATH.test(String(item.icon)) ? String(item.icon) : null,
    labelIcon: item?.labelIcon && ICON_PATH.test(String(item.labelIcon)) ? String(item.labelIcon) : null,
    name: typeof item?.name === 'string' ? item.name : '',
    effect: typeof item?.effect === 'string' ? item.effect : '',
    group: typeof item?.group === 'string' ? item.group : '',
    nowrap: Boolean(item?.nowrap),
  }));
  // A row needs a value; its label is optional (an empty name renders a plain line).
  // A row without a value is dropped on its own, never taking the whole list with it.
  const kept = normalized.filter((item) => item.effect);
  return kept.length ? kept : null;
}

export function abilityTipAttrs(model) {
  const attrs = tipAttrs(typeof model === 'object' ? { ...model, peer: model.peer || 'ability_icons' } : model);
  return attrs ? `${attrs} data-ability-tip="1" tabindex="0" role="img"` : '';
}
