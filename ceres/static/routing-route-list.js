// the route list exists on two
// screens -- the routing SETUP and the routing WINDOW. Their parts that must always be identical are built HERE, once, and both
// screens import them, so a change to the list column, its count pills or the columns of a route can no longer reach only one
// screen. tests/ceres/test_ir8543_routing_surface_parity.py renders both screens and fails if their list markup drifts apart.
import { esc } from './format.js';

// The route list itself. : it has NO wrapper and NO extra rows, so it is exactly as tall as the box beside it on both screens.
export function routeListColumnHtml(listHtml) {
  return `<div class="r3-route-index">${listHtml}</div>`;
}

// the list scrolls without a visible bar, so the summary row above it carries a small cue while the list is
// longer than its box: "showing 4-12 of 36" and two buttons that move the list a page. It costs no space in the list (the summary
// row has free room), and updateRouteScrollCue (routing-window.js) fills and hides it. The edge of the list that has more routes
// also fades (CSS classes set by the same function).
export function routeScrollCueHtml() {
  const arrow = (direction, label, glyph) => `<button type="button" class="ceres-btn ceres-btn--secondary r3-more" data-control-density="compact" data-r3-more="${direction}" aria-label="${label}">${glyph}</button>`;
  return `<span class="r3-scroll-cue" hidden><span class="r3-scroll-cue-text"></span>${arrow('up', 'Show earlier routes', '\u25B2')}${arrow('down', 'Show later routes', '\u25BC')}</span>`;
}

// One route's text parts in fixed columns (/): resource icon, resource name, arrow, destination (icon + label)
// then the percentage (setup) or the state (window). from/destination are plain text (escaped here); the icon and state
// parameters are trusted markup built by the caller.
export function routeNameHtml({ resourceIconHtml = '', from, destination, destinationIconHtml = '', stateHtml = '' }) {
  return `<span class="r3-route-name r3-route-name--icon">${resourceIconHtml || '<span></span>'}<span class="r3-from">${esc(from)}</span>`
    + `<span class="r3-route-arrow">→</span><span class="r3-to">${destinationIconHtml}${esc(destination)}</span> ${stateHtml}</span>`;
}

// The two boxes of a route's flow (/, ): the Pool box and the outcome box -- two rows, a label over a large value
// the same height -- in the approval box of the window AND in the setup's flow preview.
export function poolBoxHtml({ title, neededHtml = '' }) {
  return `<div class="r3-pool"><span>Pool</span><strong>${esc(title)}</strong>${neededHtml}</div>`;
}

export function outcomeBoxHtml({ iconHtml = '', label, valueHtml }) {
  return `<div class="r3-destination r3-destination--two">${iconHtml}<span>${esc(label)}</span><strong>${valueHtml}</strong></div>`;
}
