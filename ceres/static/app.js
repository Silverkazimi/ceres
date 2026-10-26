import { initializeSignedQueue, syncQueueHarvestEvidence } from './transaction-queue.js';
import { initRegionalDec } from './regional-dec.js';
import { mountAccountMenu } from './account-menu.js';
import { initBadgeOverlay } from './badge-overlay.js';
import { initializeButtonIcons } from './button-icons.js';
import { initializePlotLockHeaderIcon } from './plot-lock.js';
import { mountThemePicker } from './theme.js';

// pure comparison used by syncControlsDividers() below -- a divider counts as sitting at
// a wrapped line's edge when its two neighbours' vertical midpoints differ by more than 6px (i.e.
// they are not on the same line). Exported so the guard test can assert on real behaviour instead
// of re-implementing the threshold in Python.
export function dividerIsLineEdge(prevMid, nextMid) {
  return Math.abs(prevMid - nextMid) > 6;
}

// MODULE-LEVEL DECLARATION ORDER -- STRUCTURALLY CLOSED.
// `ready(fn)` is invoked at the END of this ES module. Module evaluation
// therefore completes all top-level declarations before a synchronous callback
// can enter `init()`.
// The historical tooltip, CURRENT_PLAYER, and LAST_ROOT_FONT_SIZE outages
// are recorded here as a structural hazard now closed by the EOF invocation
// every module-level `let`/`const` is initialized before the callback runs
// including values read before the first `await`.
// Keep the invocation at EOF when adding new module-level state.
// No declaration-order exception is needed for the init prefix anymore.

import { BOARD, CURRENT_PLAYER, clearBoardFilters, initFilterDropdowns, loadBoard, refreshBoardFromAvatar, showStartPage, switchPlayer, saveSortChoice, syncBoardFilterChrome } from './board.js';
import { renderCheckoutSurface } from './checkout.js';
import { ensureHazardOverlay } from './hazard.js';
import { OPERATION_POLLERS, OPERATION_POLL_SCHEDULE_MS, confirmOperationBroadcast, inFlightRecordForEntry, inFlightStoreKey, requestOperationPreview } from './operations.js';
import { handlePickerFilter, handlePickerKeydown, handleSlotEditorClick } from './picker-actions.js';
import { EXPANDED_ROWS, alignCloseAll, closeAllPlots, closeMobilePlotPicker, closeMobilePlotSheet, loadPlotDetail, mobilePlotSheetIsOpen, openAllVisiblePlots, openMobilePlotSheet, syncCloseAll, updateMountedRow } from './rows.js';
import { CERES_STAGE } from './staging.js';
import { initPulseSync } from './pulse-sync.js';
import { initPlanner } from './planner.js';
import { S } from './state.js';
import { isMobileBoardTier, maybeRenderOnBoardPresentationChange, onTableScroll, remeasureRowHeight, render, syncSortAriaState, updateHeaderHeightVar } from './table.js';
import { mountStagedSync } from './staged-sync.js';
import { initTooltips } from './tooltip.js';
import { installImageRetry } from './art.js';
import { closeHarvestModal, harvestModalCanClose, harvestModalIsOpen, initHarvest } from './harvest-modal.js';
import { closeWorksiteModal, worksiteModalIsOpen } from './worksite.js';
import { initPlotWarningHover } from './plot-warnings.js';
import { openPlotFromAddress } from './stage-navigation.js';

initializeButtonIcons();
initializePlotLockHeaderIcon();
initPlotWarningHover();
openPlotFromAddress();  // ?plot=<label> from the Cards page

function ready(fn) {
  if (document.readyState !== 'loading') fn();
  else document.addEventListener('DOMContentLoaded', fn);
}

// Position Iris against the visible board header. The hidden start-page header
// falls back to the toggle, and the top offset must not move the dock off-screen.
// (maintainer, verbatim: "the wallet slider must not be visible during the
// animation at page load") + (maintainer, verbatim: "flag button appears
// then disappears behind the loadig animation, then reappears with the full
// board. Stop that flashing, it must first be visible when the board has
// loaded"): `#edge-tabs`, `#badge-flag` and `#badge-bar` all start
// `visibility: hidden; opacity: 0` in ceres.css (pre-JS, no transition
// nothing to fade FROM until they are deliberately shown) so none of them
// ever flashes at a wrong position, or at all, while the board's own reveal
// animation plays. They are shown TOGETHER, in the same tick, once EVERY one
// of these is true
// the Wallet picker's own position has been computed at least once
// (`EDGE_TABS_POSITIONED`, set at the end of `centerThemePicker()`'s own
// edge-tabs block below)
// the badges bar's own position has been computed at least once
// (`BAR_POSITIONED`, set at the end of `centerThemePicker()`'s own
// badge-bar block below)
// the bar's own ROWS have been synced at least once (`BAR_ROWS_SYNCED`
// `ceres:badge-bar-synced`, dispatched by badge-overlay.js's
// `syncBadgeOverlay()` after its first pass -- app.js already imports
// `initBadgeOverlay` from that module, but a DOM event is used here too
// for the same reason board.js's own reveal uses one: keeping the
// reveal gate itself free of a second import just for one boolean)
// the board's own reveal has actually finished (`ceres:board-revealed`
// dispatched by board.js's `revealBoardTable()` -- board.js cannot
// import this file directly, since this file already imports FROM
// board.js).
// The start page and no-land page never dispatch `ceres:board-revealed` (the
// board is never loaded) -- all three stay hidden there regardless, via the
// SAME pre-existing `body.start-page #edge-tabs`/`body.no-land #edge-tabs`
// (and the matching `#badge-bar`/`#badge-flag` rules) `{ display: none
// !important; }` rules (ceres.css), which this reveal class does not touch.
let EDGE_TABS_POSITIONED = false;
let BAR_POSITIONED = false;
let BAR_ROWS_SYNCED = false;
let BOARD_REVEALED = false;
function maybeRevealEdgeHandles() {
  if (!EDGE_TABS_POSITIONED || !BAR_POSITIONED || !BAR_ROWS_SYNCED || !BOARD_REVEALED) return;
  const edgeTabs = document.getElementById('edge-tabs');
  if (edgeTabs && !edgeTabs.classList.contains('edge-tabs-revealed')) edgeTabs.classList.add('edge-tabs-revealed');
  const badgeBar = document.getElementById('badge-bar');
  if (badgeBar && !badgeBar.classList.contains('badge-bar-revealed')) badgeBar.classList.add('badge-bar-revealed');
  const badgeFlag = document.getElementById('badge-flag');
  if (badgeFlag && !badgeFlag.classList.contains('badge-flag-revealed')) badgeFlag.classList.add('badge-flag-revealed');
}
function initEdgeTabsReveal() {
  window.addEventListener('ceres:board-revealed', () => {
    BOARD_REVEALED = true;
    maybeRevealEdgeHandles();
  });
  window.addEventListener('ceres:badge-bar-synced', () => {
    BAR_ROWS_SYNCED = true;
    maybeRevealEdgeHandles();
  }, { once: true }); // V -- the bar's rows only need to have synced ONCE, ever
}

function anchoredThemePickerRect(trigger, picker, viewport) {
  return {
    left: viewport.right - picker.width,
    top: Math.max(viewport.top, trigger.top + trigger.height / 2 - picker.height / 2 - 10),
  };
}

function centerThemePicker() {
  const dock = document.getElementById('header');
  const headerEl = document.querySelector('header.board-header');
  const picker = document.querySelector('.theme-picker');
  if (!dock || !picker) return;
  const headerRect = headerEl?.getBoundingClientRect(); // V
  const pickerRect = picker.getBoundingClientRect(); // V
  if (!pickerRect.width || !pickerRect.height) return; // not laid out yet -- caller retries
  const pickerLayout = pickerRect; // V
  // SUPERSEDED by
  // (maintainer, verbatim: "But all icons and borders in the slider are
  // unaligned" -- measured: Iris pill 32px/28px toggle, our own wallet/flag
  // 36px/32px chip -- the old `toggleWidth + 4` guess assumed Iris's own left
  // padding was always exactly 4px and that the toggle sat flush against the
  // pill's own right edge with zero gap, neither of which this measures
  // directly). ALL of the shared handle geometry is now read straight off
  // Iris's own LIVE rects/computed style, every pass, and published as CSS
  // custom properties every other handle (`.edge-picker`/`.edge-toggle`
  // `.badge-bar-head`/`.badge-bar-head::before`) consumes instead of a
  // hand-typed copy of Iris's own values -- if Iris's own recipe ever
  // changes, ours tracks it automatically instead of drifting stale again
  // edge-w the OUTER pill's own closed width (the toggle's live width
  // plus the picker's live padding and borders; the open class
  // disappears before the closing transition finishes, so the
  // measured width itself decides whether the picker is settled)
  // edge-chip-w / --edge-chip-h the INNER chip's own real width/height
  // (`.theme-toggle`'s own rect -- measured fix: Iris's
  // real toggle is NOT square, canonical.css fixes its own
  // `height: 40px` literal while its WIDTH varies with content
  // (the emoji glyph/a future avatar image) -- a single shared
  // `--edge-chip` square value could never represent both
  // correctly at once, so width and height are two separate
  // custom properties)
  // edge-pad-left the pill's own real LEFT padding (`getComputedStyle`
  // not a hand-typed "4px" guess)
  // edge-pad-right pill's own right edge minus the toggle's own right
  // edge -- Iris's own canonical.css declares `padding: 0 0
  // 0 4px` (left only), so this is expected to measure ~0
  // but reading it live rather than assuming 0 means a
  // future Iris release that adds its own right inset is
  // still followed exactly, not silently wrong
  // edge-radius-outer / --edge-radius-inner the pill's/toggle's own
  // `border-radius`, read from computed style
  // edge-chip-shadow the toggle's own `box-shadow`, read from computed
  // style
  // `.badge-bar` (the vertical row strip's own body) is not touched directly by
  // this function -- but its own `--badge-bar-w` token (ceres.css) now resolves
  // to `var(--edge-w, ...)` (, superseding 's dedicated thinner
  // value), so it tracks the `--edge-w` this function writes above just like the
  // flag's outer box already does. (maintainer amendment: "All must be same
  // width, the single row badges as well") extends the same guarantee to every
  // per-row badge cell inside the bar -- those are plain CSS (`.badge-bar-row`
  // `.badge-overlay-row`, ceres.css), not written by this function.
  const toggle = document.querySelector('.theme-picker .theme-toggle'); // V
  const toggleRect = toggle?.getBoundingClientRect(); // V
  let closedPickerWidth = null;
  if (toggleRect && toggleRect.width > 0) {
    const pickerCs = getComputedStyle(picker); // V
    const toggleCs = getComputedStyle(toggle); // V
    const px = (value) => Number.parseFloat(value) || 0;
    closedPickerWidth = toggleRect.width
      + px(pickerCs.paddingLeft) + px(pickerCs.paddingRight)
      + px(pickerCs.borderLeftWidth) + px(pickerCs.borderRightWidth);
    // Keep the last settled geometry while Iris is opening or closing. In
    // particular, closePicker() removes `is-open` before max-width settles.
    if (Math.abs(pickerLayout.width - closedPickerWidth) <= 0.5) {
      const root = document.documentElement.style;
      root.setProperty('--edge-w', `${Math.round(closedPickerWidth)}px`); // V
      root.setProperty('--edge-chip-w', `${Math.round(toggleRect.width)}px`); // V
      root.setProperty('--edge-chip-h', `${Math.round(toggleRect.height)}px`); // V
      // (maintainer: "the badge icons are too small ... the flag must use
      // global colors"): the flag glyph's own font-size now tracks Iris's
      // toggle glyph's live-measured size instead of a fixed 0.75rem.
      root.setProperty('--edge-chip-font', toggleCs.fontSize); // V
      root.setProperty('--edge-pad-left', pickerCs.paddingLeft); // V
      root.setProperty('--edge-pad-right', `${Math.max(0, Math.round(pickerLayout.right - toggleRect.right))}px`); // V
      root.setProperty('--edge-radius-outer', pickerCs.borderRadius); // V
      root.setProperty('--edge-radius-inner', toggleCs.borderRadius); // V
      root.setProperty('--edge-chip-shadow', toggleCs.boxShadow); // V
    }
  }
  const viewportRect = document.documentElement.getBoundingClientRect(); // V
  const viewport = { left: viewportRect.left, top: 0, right: viewportRect.right, bottom: window.innerHeight }; // V
  const triggerRect = headerRect?.height
    ? headerRect
    : toggleRect?.height
      ? toggleRect
      : pickerLayout;
  const pickerPosition = anchoredThemePickerRect(triggerRect, pickerLayout, viewport);
  dock.style.top = `${pickerPosition.top}px`;
  const viewportRight = viewport.right;
  const mobileFilterSheet = dock.nextElementSibling?.matches('.controls-row.is-mobile-open')
    ? dock.nextElementSibling
    : null;
  if (mobileFilterSheet) {
    const sheetRect = mobileFilterSheet.getBoundingClientRect(); // V
    dock.style.setProperty('--mobile-filter-sheet-top', `${sheetRect.top + mobileFilterSheet.clientTop}px`); // V
    const sheetBorderRight = parseFloat(getComputedStyle(mobileFilterSheet).borderRightWidth) || 0; // V
    const sheetInnerRight = sheetRect.right - sheetBorderRight; // V
    const rightInset = Math.max(0, viewportRight - sheetInnerRight); // V
    dock.style.setProperty('--mobile-filter-sheet-right', `${rightInset}px`); // V
  }
  // his instruction, marked important by him: "the header buttons must never be
  // hiding behind the iris theme picker". The picker is a FIXED widget from another
  // project that we may not restyle, so the header yields space instead -- and the
  // reservation is MEASURED here rather than guessed as a CSS constant, because neither
  // side is static: the checkout button appears only when work is staged (so the right
  // cluster grows during exactly the workflow where its buttons matter most), and the
  // picker's own width is a runtime value. A fixed padding is a guess that goes wrong
  // the first time either side changes.
  const gap = 12;
  // (maintainer, verbatim, high: "you broke iris -- all three sliders must sit
  // on the furthest right edge of the page. zero gap. the design is made for
  // that."): `viewportRight` is still needed for the `--hdr-pad-right` reserve
  // below (a fixed element's `right: 0` resolves against the true layout viewport
  // NOT `document.documentElement.clientWidth`, which excludes the scrollbar
  // see the second measured follow-up this codebase already carries for
  // the full reasoning) -- but the Iris dock and `#edge-tabs`'s own `right` no
  // longer needs a JS-computed gutter AT ALL: they are plain CSS `right: 0` now
  // (ceres.css), restoring the vendored Iris picker's own unedited behaviour
  // exactly, per the maintainer's own wording ("zero gap. the design is made for
  // that."). Team-lead correction: the maintainer's "all three sliders" are the Iris
  // dock, the wallet picker (`#edge-tabs`) and the badges flag/bar -- `#scroll
  // top` is NOT one of them, and sharing the bar's own edge column re-creates
  // the measured overlap of the bar's bottom rows; it keeps its own
  // `right: calc(var(--badge-bar-w) + 8px)` CSS formula (ceres.css), one
  // handle-width left of the bar. The JS writes that used to set `.style.right`
  // on the dock/edge-tabs (the /measured-follow-up "gutter" this comment
  // block used to describe at length) are gone.
  // HIGH FIX, caught in verification: the reservation used `innerWidth - pickerRect.left`
  // which is only correct once the picker has settled at its final right edge. It is
  // measured at load and once more after a 300ms retry, and nothing re-measures after
  // that -- so a picker that settles late left the padding at 26px instead of 44px and
  // the checkout button ended up under it. The closed width derived above is stable
  // while the tray opens and closes, so using it with the dock's viewport-edge distance
  // keeps the header reserve fixed through both transitions.
  const edge = Math.max(0, viewportRight - pickerLayout.right); // V
  const reserve = Math.ceil((closedPickerWidth ?? pickerLayout.width) + edge + gap); // V
  if (headerEl && headerRect?.height) headerEl.style.setProperty('--hdr-pad-right', `${Math.max(reserve, 16)}px`);
  // Dock is `position: fixed` -- its `top` is a direct viewport coordinate
  // no offset subtraction against an ancestor's rect needed (unlike the old
  // `position: absolute` dock, whose containing block was an ancestor box).
  // 's "resource slider must sit centered in the filter row" is still the
  // shape; changed WHAT the row does under scroll (see updateEdgeTabsPosition()
  // below, and `.controls-row`'s own ceres.css comment, for the full mechanism this
  // call now needs). `right` still needs no JS write (, above) -- CSS `right: 0`
  // on `#edge-tabs` itself.
  updateEdgeTabsPosition();
  // the bar is `position: absolute; right: 0` on <body> (ceres.css), flush with
  // the true page edge and the Iris/Wallet handles. JS writes only its top and height.
  // never write its `right` from the table's rect -- the table overhangs its
  // clipped scroller, and following that overhang pushed the bar past the page edge
  // (), which gave the page an empty horizontal overflow and a scrollbar.
  const badgeBarEl = document.getElementById('badge-bar'); // V
  const scrollerEl = document.getElementById('plot-table-scroll'); // V
  const scrollerRect = scrollerEl?.getBoundingClientRect(); // V
  if (badgeBarEl && scrollerRect && scrollerRect.width > 0) {
    const pageGutter = Math.max(0, viewportRight - scrollerRect.right); // V
    document.documentElement.style.setProperty('--page-gutter', `${Math.round(pageGutter)}px`); // V
    const barDocTop = scrollerRect.top + window.scrollY; // V
    badgeBarEl.style.top = `${barDocTop}px`; // V
    badgeBarEl.style.height = `${scrollerRect.height}px`; // V
    BAR_POSITIONED = true; // see maybeRevealEdgeHandles() above
    maybeRevealEdgeHandles();
  }
  // The flag (`#badge-flag`) is a genuinely SEPARATE fixed element now (see
  // `updateBadgeFlagPosition()` below) -- it needs its own scroll-tracking, not
  // just this function's own layout-event triggers, so its position logic lives
  // in its own small function, called from here AND from a dedicated scroll
  // listener (`initBadgeFlag()`).
  updateBadgeFlagPosition();
}

// (maintainer, verbatim: "the wallet and flag slider are still not sticky in
// their respective rows"): `#edge-tabs` (ceres.css `.edge-tabs`) is `position: fixed`
// now -- `.controls-row` (the filter row it centres on) is itself `position: sticky`
// under the page header (ceres.css, same rule), so a document-coordinate `top`
// (`rect.top + window.scrollY`, the old -era formula) would drift further and
// further past the row's own true screen position once it sticks, the exact same
// failure mode `#badge-flag` had against `#table-head-sticky` before . Reading
// `.controls-row`'s live, viewport-relative `getBoundingClientRect()` and writing it
// straight to a `position: fixed` `top` (no `+ window.scrollY`) is correct whether the
// row is mid-scroll or already stuck, exactly like `updateBadgeFlagPosition()` below
// this function follows the SAME pattern and needs the same direct (non-rAF-wrapped)
// scroll listener (`initEdgeTabsScrollSync()`), not the layout-event-only trigger
// `centerThemePicker()` still uses for everything else in that function (the header
// Iris dock and badge bar positions do not need per-scroll-frame tracking -- see
// their own comments).
function updateEdgeTabsPosition() {
  const edgeTabs = document.getElementById('edge-tabs');
  const controlsRow = document.querySelector('.controls-row');
  const walletPickerEl = document.getElementById('wallet-picker');
  if (!edgeTabs || !controlsRow || !walletPickerEl) return;
  const controlsRect = controlsRow.getBoundingClientRect(); // V
  // (maintainer): 2px free above and below the wallet box, on whole pixels; the row's 1px bottom border is not free space.
  const boxTop = Math.round(controlsRect.top) + 2;
  const boxBottom = Math.round(controlsRect.bottom - 1) - 2;
  if (boxBottom - boxTop > 8) document.documentElement.style.setProperty('--edge-row-h', `${boxBottom - boxTop}px`);
  const walletPickerHeight = walletPickerEl.getBoundingClientRect().height; // V
  if (!(controlsRect.height > 0) || !(walletPickerHeight > 0)) return;
  const edgeTabsY = boxBottom - boxTop > 8 ? boxTop : controlsRect.top + controlsRect.height / 2 - walletPickerHeight / 2; // V
  edgeTabs.style.top = `${edgeTabsY}px`; // V
  EDGE_TABS_POSITIONED = true; // see maybeRevealEdgeHandles() above
  maybeRevealEdgeHandles();
}

function initEdgeTabsScrollSync() {
  const edgeTabs = document.getElementById('edge-tabs');
  if (!edgeTabs) return;
  window.addEventListener('scroll', () => {
    updateEdgeTabsPosition();
  }, { passive: true });
}

// The flag mirrors the full measured thead box. Its fixed position follows the
// sticky header on each scroll event so its edge and bottom remain continuous.
function updateBadgeFlagPosition() {
  const flag = document.getElementById('badge-flag');
  const thead = document.getElementById('table-head-sticky');
  if (!flag || !thead) return;
  const theadRect = thead.getBoundingClientRect(); // V
  if (!theadRect.height) return; // not laid out yet
  const controlsRow = document.querySelector('.controls-row'); // V
  const wallet = document.getElementById('wallet-picker'); // V
  const controlsRect = controlsRow?.getBoundingClientRect(); // V
  const walletRect = wallet?.getBoundingClientRect(); // V
  // (maintainer): 2px free above the flag; below it the header's 1px shadow plus 2px (3px), on whole pixels.
  // The column's shade starts under that shadow so it runs unbroken across the badges column.
  const flagTop = Math.round(theadRect.top) + 2;
  const flagHeight = Math.max(0, Math.round(theadRect.bottom) - 3 - flagTop);
  document.documentElement.style.setProperty('--badge-flag-inset', `${flagTop - theadRect.top}px`);
  document.documentElement.style.setProperty('--badge-flag-h', `${flagHeight}px`);
  document.documentElement.style.setProperty('--badge-head-shade', `${theadRect.height + 1}px`);
  flag.style.top = `${flagTop}px`; // V
  document.documentElement.style.setProperty('--badge-flag-top', `${flagTop}px`); // the flag's status tray shares its row
  // no JS `right` -- ceres.css `position: fixed; right: 0` keeps the flag on
  // the true page edge, aligned with the bar beneath it.
}

function initBadgeFlag() {
  const flag = document.getElementById('badge-flag');
  if (!flag) return;
  // (maintainer, verbatim: "same for the flag, it must be sticky on the table
  // header, even when scrolling"): MEASURED -- wrapping the update in its own
  // `requestAnimationFrame` (the previous approach, like `scheduleCenterThemePicker()`
  // elsewhere in this file) delays the write to the frame AFTER the one the browser's
  // native compositor already used to paint `#table-head-sticky`'s new sticky
  // position, so the fixed flag trailed the real thead by a consistent one scroll
  // step (~10px at typical wheel deltas) for the whole approach into the sticky
  // band, only catching up once the thead reached its fully clamped `top`. Reading
  // `thead.getBoundingClientRect()` and writing `flag.style.top`/`height` directly
  // inside the `scroll` handler -- no extra rAF hop -- applies in the SAME frame the
  // browser is about to paint, eliminating the drift: a Playwright measurement
  // stepping scrollY by 10px through the sticking transition (4600px-4750px document
  // offset on the real board) found `flag.top === thead.top` (diff 0) at every step
  // after this change, versus a steady 10px lag before it. The read+write pair here
  // is a single getBoundingClientRect() and two style writes -- cheap enough per
  // scroll event that no throttling is needed; unlike `centerThemePicker()` (a much
  // larger layout read), there is nothing here worth debouncing.
  window.addEventListener('scroll', () => {
    updateBadgeFlagPosition();
  }, { passive: true });
}

let CENTER_PICKER_RAF = null;
function scheduleCenterThemePicker() {
  if (CENTER_PICKER_RAF) cancelAnimationFrame(CENTER_PICKER_RAF);
  CENTER_PICKER_RAF = requestAnimationFrame(() => {
    CENTER_PICKER_RAF = null;
    centerThemePicker();
  });
}

// (maintainer, verbatim: "alignment fix not visible in latest version with
// hard refresh" -- his screenshot on showed the Iris/Wallet/flag
// handles unaligned again): 's own measurement only re-runs on
// `resize`/`ceres:table-layout`/the load-time retry passes -- none of which
// fire when `.theme-picker`/`.theme-toggle` change size on their OWN (a late
// avatar image finishing its layout inside the toggle, a font/emoji glyph
// metric settling after its own font-face finishes loading, or any future
// change to Iris's own vendored recipe), leaving `--edge-w`/`--edge-chip`
// frozen at whatever the toggle measured at the LAST trigger. A
// `ResizeObserver` on both `.theme-picker` (the outer pill) and
// `.theme-toggle` (the inner chip) catches every one of those cases
// directly, regardless of what caused the resize -- `scheduleCenterThemePicker()`
// (already rAF-debounced, above) is reused rather than a second debounce
// mechanism. Skipped while `.is-open` (the tray's own leftward expansion is a
// much bigger, deliberate width change this observer must not chase
// `centerThemePicker()`'s own internal `!picker.classList.contains('is-open')`
// guard already protects the actual `--edge-w`/`--edge-chip` publish, but
// skipping the OBSERVER callback itself avoids a stray recompute storm while
// the tray's own `max-width` transition is actively resizing it every frame).
// A `load` listener on any `<img>` inside the toggle is defensive/future-proofing
// theme.js's own vendored toggle carries only emoji text today (no `<img>`)
// but a later Iris release or a project-local change that mounts an avatar
// image into it (as the maintainer's own report describes) would otherwise need
// its own dedicated wiring; this one line covers that case for free, with no
// image present today, this listener simply attaches to zero elements and
// does nothing.
let EDGE_GEOMETRY_OBSERVER = null;
function initEdgeGeometryObserver() {
  if (EDGE_GEOMETRY_OBSERVER) {
    EDGE_GEOMETRY_OBSERVER.disconnect();
    EDGE_GEOMETRY_OBSERVER = null;
  }
  const picker = document.querySelector('.theme-picker');
  const toggle = document.querySelector('.theme-picker .theme-toggle');
  if (!picker) return;
  const onResize = () => {
    if (picker.classList.contains('is-open')) return;
    scheduleCenterThemePicker();
  };
  const onTransitionEnd = (event) => {
    if (event.propertyName !== 'max-width' || picker.classList.contains('is-open')) return;
    requestAnimationFrame(() => requestAnimationFrame(scheduleCenterThemePicker));
  };
  const observer = new ResizeObserver(onResize);
  observer.observe(picker);
  picker.querySelector('.theme-tray')?.addEventListener('transitionend', onTransitionEnd);
  if (toggle) {
    observer.observe(toggle);
    toggle.querySelectorAll('img').forEach((img) => {
      img.addEventListener('load', onResize);
    });
  }
  EDGE_GEOMETRY_OBSERVER = observer;
}

// theme.js's mountThemePicker() calls its own `_unmount()` first, which
// removes the exact `.theme-picker`/`.theme-toggle` nodes initEdgeGeometryObserver()
// is watching -- a second mount (today: called once at startup, but latent for any
// future re-mount) would leave the ResizeObserver holding detached elements forever.
// theme.js is vendored Iris canonical code (see its own header) and is not edited
// here; instead this watches the picker's own host container for childList changes
// and re-runs initEdgeGeometryObserver() whenever `.theme-picker` is replaced.
function watchThemePickerRemount() {
  const host = document.querySelector('#header .container, #header, .header') || document.body;
  const mutationObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.addedNodes.length || mutation.removedNodes.length) {
        initEdgeGeometryObserver();
        break;
      }
    }
  });
  mutationObserver.observe(host, { childList: true });
}

// (maintainer, verbatim: "add a scroll up button to the bottom right in the iris
// style"): a passive scroll listener (never blocks the scroll itself), rAF-throttled
// so the visibility toggle -- the only DOM write per scroll frame -- runs at most
// once per paint regardless of how many 'scroll' events fire in between. Click
// scrolls to the very top; `prefers-reduced-motion` drops the smooth animation, not
// the scroll itself (an instant jump is still fully functional).
let SCROLL_TOP_RAF = null;
function initScrollTop() {
  const btn = document.getElementById('scroll-top');
  if (!btn) return;
  const update = () => {
    SCROLL_TOP_RAF = null;
    btn.classList.toggle('is-visible', window.scrollY > 400); // V
  };
  window.addEventListener('scroll', () => {
    if (SCROLL_TOP_RAF) return;
    SCROLL_TOP_RAF = requestAnimationFrame(update);
  }, { passive: true });
  update(); // V initial state -- a deep-linked/restored scroll position starts visible if already past the threshold
  btn.addEventListener('click', () => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
  });
}

let MOBILE_FILTER_RETURN_TARGET = null;

function closeMobileFilters({ restoreFocus = false } = {}) {
  const controls = document.getElementById('board-controls');
  if (!controls) return;
  controls.classList.remove('is-mobile-open', 'is-search-mode');
  controls.removeAttribute('role');
  controls.removeAttribute('aria-modal');
  controls.removeAttribute('aria-labelledby');
  document.getElementById('mobile-filter-backdrop').hidden = true;
  document.getElementById('mobile-filters-btn')?.setAttribute('aria-expanded', 'false');
  if (restoreFocus) MOBILE_FILTER_RETURN_TARGET?.focus();
  MOBILE_FILTER_RETURN_TARGET = null;
}

function openMobileFilters(opener) {
  if (!isMobileBoardTier()) return;
  const controls = document.getElementById('board-controls');
  if (!controls) return;
  MOBILE_FILTER_RETURN_TARGET = opener;
  controls.classList.add('is-mobile-open');
  centerThemePicker();
  controls.setAttribute('role', 'dialog');
  controls.setAttribute('aria-modal', 'true');
  controls.setAttribute('aria-labelledby', 'mobile-filter-sheet-title');
  document.getElementById('mobile-filter-sheet-title').textContent = 'Filters';
  document.getElementById('mobile-filter-backdrop').hidden = false;
  document.getElementById('mobile-filters-btn')?.setAttribute('aria-expanded', 'true');
  requestAnimationFrame(() => (document.getElementById('region-filter')?._csel?.trigger)?.focus());
}

function initMobileShell() {
  const controls = document.querySelector('.controls-row');
  if (controls) controls.id = 'board-controls';
  const filters = document.getElementById('mobile-filters-btn');
  const actions = document.getElementById('mobile-action-bar');
  filters?.addEventListener('click', () => openMobileFilters(filters));
  actions?.addEventListener('click', handleSlotEditorClick);
  document.getElementById('mobile-filters-close')?.addEventListener('click', () => closeMobileFilters({ restoreFocus: true }));
  document.getElementById('mobile-filter-backdrop')?.addEventListener('click', () => closeMobileFilters({ restoreFocus: true }));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && document.getElementById('board-controls')?.classList.contains('is-mobile-open')) closeMobileFilters({ restoreFocus: true });
  });
}

function closeMobilePlotAndHistory({ restoreFocus = true, closeSelection = true } = {}) {
  if (closeSelection && closeMobilePlotPicker()) return;
  const ownsHistoryEntry = history.state?.ceresMobilePlotSheet === true;
  if (!closeMobilePlotSheet({ restoreFocus })) return;
  if (!ownsHistoryEntry) return;
  // the phone sheet scrolls with the page, so the browser's own restore on history.back() would put the board
  // back at the top; re-apply the board position closeMobilePlotSheet() just restored once that step is done.
  const boardY = window.scrollY; // V (no page zoom)
  window.addEventListener?.('popstate', () => globalThis.requestAnimationFrame?.(() => window.scrollTo?.(0, boardY)), { once: true });
  history.back();
}

function closeTopMobileOverlay() {
  if (worksiteModalIsOpen()) { closeWorksiteModal(); return 'closed'; }
  if (!harvestModalIsOpen()) return 'none';
  if (!harvestModalCanClose()) return 'blocked';
  closeHarvestModal();
  return 'closed';
}

function pushMobilePlotHistoryMarker() {
  history.pushState({ ...(history.state || {}), ceresMobilePlotSheet: true }, '');
}

function initMobilePlotSheet() {
  const cards = document.getElementById('plot-card-list');
  const openCard = (card) => {
    if (!card || mobilePlotSheetIsOpen()) return;
    if (!openMobilePlotSheet(card.dataset.deedUid, card)) return;
    pushMobilePlotHistoryMarker();
  };
  cards?.addEventListener('click', (event) => openCard(event.target.closest('.plot-card')));
  cards?.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    const card = event.target.closest('.plot-card');
    if (!card) return;
    event.preventDefault();
    openCard(card);
  });
  document.getElementById('mobile-plot-sheet-close')?.addEventListener('click', () => closeMobilePlotAndHistory());
  document.getElementById('mobile-plot-sheet-body')?.addEventListener('click', (event) => {
    const retry = event.target.closest('.expand-retry-link');
    if (!retry) return;
    event.preventDefault();
    const row = BOARD?.plots.find((plot) => plot.deed_uid === retry.dataset.deedUid);
    if (row) loadPlotDetail(row);
  });
  document.getElementById('mobile-plot-sheet-body')?.addEventListener('click', handleSlotEditorClick);
  document.getElementById('mobile-plot-sheet-body')?.addEventListener('input', handlePickerFilter);
  document.getElementById('mobile-plot-sheet-body')?.addEventListener('change', handlePickerFilter);
  window.addEventListener('popstate', () => {
    if (!mobilePlotSheetIsOpen()) return;
    if (closeTopMobileOverlay() !== 'none') { pushMobilePlotHistoryMarker(); return; }
    if (closeMobilePlotPicker()) { pushMobilePlotHistoryMarker(); return; }
    closeMobilePlotSheet();
    const boardY = window.scrollY; // V (no page zoom) -- keep the restored board position over the browser's own restore
    globalThis.requestAnimationFrame?.(() => window.scrollTo?.(0, boardY));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || event.defaultPrevented || !mobilePlotSheetIsOpen()) return;
    const overlay = closeTopMobileOverlay();
    if (overlay !== 'none') { event.preventDefault(); return; }
    closeMobilePlotAndHistory();
  });
}

async function init() {
  initPulseSync(); // all stage pulses beat together
  initFilterDropdowns();
  initMobileShell();
  initScrollTop();
  initBadgeFlag();
  initEdgeTabsScrollSync();
  initEdgeTabsReveal();
  initMobilePlotSheet();
  initHarvest();
  initPlanner(); // phase 5
  const playerInputEarly = document.getElementById('player-input');
  if (CURRENT_PLAYER) playerInputEarly.value = CURRENT_PLAYER;
  updateHeaderHeightVar();
  const headerEl = document.querySelector('header.board-header');
  if (headerEl && typeof ResizeObserver === 'function') {
    new ResizeObserver(updateHeaderHeightVar).observe(headerEl);
  }
  headerEl?.addEventListener('click', handleSlotEditorClick);
  document.getElementById('board-notices')?.addEventListener('click', handleSlotEditorClick);
  document.addEventListener('keydown', handlePickerKeydown);
  // the table is sized by CSS (ceres.css `table.plot-table`), so a resize
  // re-lays it out by itself -- no column refit, no relock, no side-scroll sync.
  // The one JS measurement left is the row height for the virtual scroller
  // re-read at most once per frame when the window or the table's box changes.
  let rowHeightFrame = 0;
  const scheduleRowHeight = () => {
    if (rowHeightFrame) return;
    rowHeightFrame = requestAnimationFrame(() => { rowHeightFrame = 0; remeasureRowHeight(); });
  };
  window.addEventListener('resize', () => {
    window.ceresScale?.apply();
    if (!isMobileBoardTier() && mobilePlotSheetIsOpen()) closeMobilePlotAndHistory({ restoreFocus: false, closeSelection: false });
    maybeRenderOnBoardPresentationChange();
    if (!isMobileBoardTier()) closeMobileFilters();
    updateHeaderHeightVar();
    scheduleRowHeight();
  });
  const scrollerEl = document.getElementById('plot-table-scroll');
  if (scrollerEl && 'ResizeObserver' in window) new ResizeObserver(scheduleRowHeight).observe(scrollerEl);
  // the header row is #plot-table's own sticky <thead> again (the separate
  // head-mirror table and its scroll sync are gone).
  syncSortAriaState(); // reflect S.SORT_KEY/SORT_DIR before the first render too
  document.querySelectorAll('#plot-table thead [data-key]').forEach((control) => {
    if (control.dataset.sortBound) return;
    control.addEventListener('click', () => {
      const key = control.dataset.key;
      // 'plot' is a stable identity column, always sortable; every other clickable
      // header's data-key must be a rank/numeric field from the sortable allowlist
      // in particular rarity_rank/plot_status_rank, never the raw display string, so
      // sorting stays semantic (Legendary>Epic>Rare>Common / Occupied>Magical>Natural).
      if (!BOARD?.sortable?.includes(key) && key !== 'plot') return;
      if (S.SORT_KEY === key) S.SORT_DIR *= -1; else { S.SORT_KEY = key; S.SORT_DIR = -1; }
      saveSortChoice();
      syncSortAriaState();
      render();
    });
    control.dataset.sortBound = 'true';
  });
  document.getElementById('close-all-plots')?.addEventListener('click', () => closeAllPlots());
  document.getElementById('open-all-plots')?.addEventListener('click', () => openAllVisiblePlots());
  document.querySelector('.controls-row')?.addEventListener('change', () => queueMicrotask(syncCloseAll)); // the filters decide what "all" is
  window.addEventListener('resize', alignCloseAll);
  // a filter-row divider only counts between two visible neighbours on the SAME line
  // when the row wraps (1280–1919) a divider at a line edge is hidden (same idiom as syncSortDivider, ).
  const syncControlsDividers = () => {
    const mid = (el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; }; // V
    const visible = (el) => el && !el.hidden && el.getClientRects().length > 0;
    document.querySelectorAll('.controls-row .controls-sep').forEach((sep) => {
      sep.classList.remove('is-line-edge');
      let prev = sep.previousElementSibling; while (prev && !visible(prev)) prev = prev.previousElementSibling;
      let next = sep.nextElementSibling; while (next && !visible(next)) next = next.nextElementSibling;
      if (!prev || !next) { sep.classList.add('is-line-edge'); return; }
      // The divider is stretched to the line, so compare its neighbours with each other, not with it.
      sep.classList.toggle('is-line-edge', dividerIsLineEdge(mid(prev), mid(next)));
    });
  };
  syncControlsDividers();
  window.addEventListener('resize', syncControlsDividers);
  if (typeof ResizeObserver === 'function') {
    const row = document.querySelector('.controls-row');
    // Pre-landing review fix ( v6, round 3): this observer already watches
    // `.controls-row` for `syncControlsDividers()` -- reused (not a second observer)
    // to also recompute `#edge-tabs`'s position (`scheduleCenterThemePicker()`)
    // whenever the row's own box changes height, and ALSO watches
    // `#board-notices`/`.totals-strip` (the two elements directly above the row) so a
    // notice appearing/disappearing or the totals strip filling in -- neither of
    // which resizes `.controls-row` itself, but both shift ITS vertical position
    // still triggers a recompute.
    const onControlsAreaResize = () => { syncControlsDividers(); scheduleCenterThemePicker(); };
    const controlsAreaObserver = new ResizeObserver(onControlsAreaResize);
    if (row) controlsAreaObserver.observe(row);
    document.getElementById('board-notices') && controlsAreaObserver.observe(document.getElementById('board-notices'));
    document.querySelector('.totals-strip') && controlsAreaObserver.observe(document.querySelector('.totals-strip'));
    document.querySelector('header.board-header') && controlsAreaObserver.observe(document.querySelector('header.board-header')); // a taller app header shifts the row without resizing it
  }
  // Look 3 (maintainer-accepted plan, Variant A -- "Clear: All filters, not Sort")
  // "Clear all" only shows/hides in step with the row's own filter controls -- one
  // delegated listener, the same pattern as the syncCloseAll one just above, rather
  // than a per-field handler for every one of the nine filter dimensions. This is a
  // NEW listener; the existing per-field render() listeners further below (and this
  // syncCloseAll one) are untouched.
  document.querySelector('.controls-row')?.addEventListener('input', syncBoardFilterChrome);
  document.querySelector('.controls-row')?.addEventListener('change', syncBoardFilterChrome);
  document.getElementById('clear-board-filters')?.addEventListener('click', () => clearBoardFilters());
  if (CURRENT_PLAYER) await loadBoard(); else showStartPage(); // no player in the URL -> start page, no table
  syncCloseAll();
  window.ceresScale?.apply();
  ensureHazardOverlay();
  // Item 23 -- the owner: "clicking outside the choutout modal must instantly
  // close it." CAPTURE-phase document listener (the `true` third arg)
  // NOT bubble phase. handleSlotEditorClick's own delegated handlers (bound
  // on `#plot-tbody`/`headerEl`) call `event.stopPropagation()` for every
  // `[data-slot-action]`/etc. click, e.g. clicking a worker tile in the main
  // table -- a real "outside the modal" click that must still close it. A
  // bubble-phase listener here never sees that click at all once
  // stopPropagation runs (confirmed live: a first bubble-phase attempt
  // silently failed to close on exactly this case). Capture runs top-down
  // BEFORE any bubble-phase stopPropagation takes effect, so this always
  // sees the click regardless of what any inner handler does afterward.
  // `.contains()` on `.checkout-dock` is enough to tell "inside" (the
  // button, or any control inside the panel) from "outside" -- no
  // coordination needed with any other handler's own propagation. Closing
  // only ever flips `CERES_CHECKOUT_OPEN` and re-renders -- CERES_STAGE (the
  // actual staged moves) is never touched here, so closing the panel (by
  // outside click OR Escape, same code path) never discards a batch
  // verified live (see CHANGELOG).
  document.addEventListener('click', (event) => {
    if (!S.CERES_CHECKOUT_OPEN) return;
    const dock = document.getElementById('checkout-dock');
    if (dock && dock.contains(event.target)) return;
    if (event.target.closest?.('.chain-confirm-overlay, .combine-dialog-overlay.is-plan-mode')) return; // child review keeps Changes open.
    if (document.getElementById('checkout-panel')?.contains(event.target)) return; // inside the modal
    S.CERES_CHECKOUT_OPEN = false;
    renderCheckoutSurface();
  }, true);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && S.CERES_CHECKOUT_OPEN) {
      S.CERES_CHECKOUT_OPEN = false;
      renderCheckoutSurface();
      (isMobileBoardTier() ? document.getElementById('mobile-changes-btn') : document.getElementById('checkout-btn'))?.focus(); // phase 4: focus returns to Changes
    }
  });
  // Worker picker rows are a <div role="button"> (renderCandidateRow(), 2026-08-30
  // worker-picker-layout fix) so their trailing actions column can be a real
  // nested <button> -- a native <button> would forbid that. Divs get no free
  // Enter/Space activation, so this restores it. Scoped to `[role="button"]`
  // (an attribute only these divs carry) so a real <button>'s own native
  // activation is never double-fired.
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (event.target.closest('button, a, input, select, textarea')) return;
    const target = event.target.closest('[role="button"][data-picker-action="choose"]');
    if (!target) return;
    event.preventDefault();
    target.click();
  });
  document.getElementById('slot-hazard-overlay').addEventListener('click', handleSlotEditorClick);
  window.addEventListener('scroll', onTableScroll, { passive: true });
  // Delegated (rows are (re)rendered continually, the Retry link doesn't exist
  // until an expand actually fails) -- see workerSubtable()'s EXPAND_ERRORS
  // branch and loadPlotDetail() above.
  document.getElementById('plot-tbody').addEventListener('click', (e) => {
    const link = e.target.closest('.expand-retry-link');
    if (!link) return;
    e.preventDefault();
    e.stopPropagation();
    const row = BOARD.plots.find((p) => p.deed_uid === link.dataset.deedUid);
    if (row) loadPlotDetail(row);
  });
  document.getElementById('plot-tbody').addEventListener('click', handleSlotEditorClick);
  document.getElementById('plot-tbody').addEventListener('input', handlePickerFilter);
  document.getElementById('plot-tbody').addEventListener('change', handlePickerFilter);
  const playerInput = document.getElementById('player-input');
  playerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') switchPlayer(playerInput.value);
  });
  playerInput.addEventListener('blur', () => switchPlayer(playerInput.value));
  document.getElementById('player-go-btn').addEventListener('click', () => switchPlayer(playerInput.value));
  const avatar = document.getElementById('player-avatar');
  avatar?.addEventListener('click', () => refreshBoardFromAvatar());
  // v0.4.13: wrapped in arrow functions, not passed as bare `render` -- addEventListener
  // would otherwise call render(event), and render()'s new isNewDataset parameter would
  // see the (truthy) Event object and wrongly treat every filter keystroke/toggle as a
  // dataset change (page-scroll-to-top + column re-measure, the exact S1/S3 bugs this
  // batch fixes). These are pure filter re-renders over the SAME dataset.
  document.getElementById('text-filter').addEventListener('input', () => render());
  document.getElementById('region-filter').addEventListener('change', () => render());
  document.getElementById('rarity-filter').addEventListener('change', () => render());
  document.getElementById('terrain-filter').addEventListener('change', () => render());
  document.getElementById('resource-filter').addEventListener('change', () => render());
  document.getElementById('status-filter').addEventListener('change', () => render());
  document.getElementById('badge-filter').addEventListener('change', () => render());
  document.getElementById('staged-only')?.addEventListener('change', () => render());
  // shown once the page is a screen down.
  const scrollTop = document.getElementById('scroll-top-btn');
  if (scrollTop) {
    const syncScrollTop = () => { scrollTop.hidden = window.scrollY < window.innerHeight * 0.9; }; // V (no page zoom)
    window.addEventListener('scroll', syncScrollTop, { passive: true });
    scrollTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }));
    syncScrollTop();
  }
  // the phone's badges-on-cards overlay is the viewer's own choice, remembered on this device.
  const cardBadges = document.getElementById('card-badges');
  if (cardBadges) {
    try { cardBadges.checked = localStorage.getItem('ceres:card-badges') === '1'; } catch (_) { /* storage blocked: off */ }
    document.documentElement.classList.toggle('ceres-card-badges', cardBadges.checked);
    cardBadges.addEventListener('change', () => {
      document.documentElement.classList.toggle('ceres-card-badges', cardBadges.checked);
      try { localStorage.setItem('ceres:card-badges', cardBadges.checked ? '1' : '0'); } catch (_) { /* storage blocked */ }
    });
  }
}


// candidate: the "table lines run through the badge column" variant is opt-in only, for the
// maintainer to compare with the frozen approved look. Without `?badges=candidate` nothing is added.
if (new URLSearchParams(location.search).get('badges') === 'candidate') document.body.classList.add('badges-candidate');

if (new URLSearchParams(location.search).get('ir1882-guard') === '1') {
  Object.assign(globalThis, {
    CERES_STAGE, OPERATION_POLLERS, OPERATION_POLL_SCHEDULE_MS,
    inFlightStoreKey, inFlightRecordForEntry, requestOperationPreview,
    confirmOperationBroadcast,
  });
}

// the header's Cards button opens the read-only card browser for the player on screen.
// it is now the switcher's Cards half (a link; header-icons.js sets its href from the address). The player on
// screen wins over the address, which a player switch does not rewrite before the board has loaded.
function openCardBrowser(event) {
  if (event && (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return; // a new tab keeps the link
  event?.preventDefault();
  location.href = CURRENT_PLAYER ? `/cards?player=${encodeURIComponent(CURRENT_PLAYER)}` : '/cards';
}

ready(() => {
  mountThemePicker({ storageKey: 'ceres:theme', defaultTheme: 'dark' });
  document.getElementById('page-switch-cards')?.addEventListener('click', openCardBrowser);
  initEdgeGeometryObserver();
  watchThemePickerRemount();
  initTooltips();
  installImageRetry(); // every <img> that fails retries with backoff
  const initDone = init();
  // Two follow-up passes catch the picker's final size after font/icon-glyph
  // loading settles.
  scheduleCenterThemePicker();
  setTimeout(scheduleCenterThemePicker, 300);
  // Pre-landing review fix ( v6 amendment): #edge-tabs's own vertical position
  // now depends on `.controls-row`'s measured box (centerThemePicker()'s own
  // `controlsRect`), which can still be settling from font/icon loading at the two
  // passes above -- `init()` `await`s `loadBoard()` internally, so this recomputes
  // once more right after the board's first paint, when the row's real content
  // (filter option counts, the Clear-all button's visibility, etc.) has actually
  // landed.
  initDone.then(scheduleCenterThemePicker);
  window.addEventListener('resize', scheduleCenterThemePicker);
  // Pre-landing review fix, round 3: notices/totals above the filter row ('s
  // `#edge-tabs` now depends on `.controls-row`'s measured position) change height on
  // every render() -- a new notice line, the totals strip filling in -- which shifts
  // that row's own vertical position without firing `resize`. table.js has no
  // reference back to this scheduler, so it dispatches `ceres:table-layout` at the
  // end of every render() instead; caught here.
  window.addEventListener('ceres:table-layout', scheduleCenterThemePicker);
});

ready(initRegionalDec);
ready(mountAccountMenu);
ready(mountStagedSync);
ready(initBadgeOverlay);

function recoverSignedTransactions() {
  if (!CURRENT_PLAYER) return;
  initializeSignedQueue(CURRENT_PLAYER);
  syncQueueHarvestEvidence(CURRENT_PLAYER).catch(() => {});
}
ready(() => {
  recoverSignedTransactions();
  setInterval(recoverSignedTransactions, 15000);
  window.addEventListener('ceres:player-changed', recoverSignedTransactions);
});
