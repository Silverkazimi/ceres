import { plannedPlacementReason } from './candidates.js';
import { chainReturn } from './combine-chain.js';
import { chainPlans } from './stage-plans.js';
import { handleUpgradeAction, loadOccupantUpgrade } from './combine-chain-ui.js';
import { revealStagedChange } from './stage-navigation.js';
import { openFillPlotModal } from './fill-plot.js';
import { bloodlinePlotSubTypes } from './badges.js';
import { BOARD, BOARD_GENERATION, CURRENT_PLAYER, catalogNotice, noticeMessage, playerQueryAppend } from './board.js';
import { candidateDelta, candidateQuery, candidateStableKey, pickerEditionOptionsHtml, pickerFiltersActive, pickerRegionHtml, signedPp, workerUpgradeIsAvailable, workerUpgradeOptionsFor } from './candidates.js';
import { constructionModel } from './feed-changes.js';
import { checkoutBlockedInFlightEntries, checkoutSignableEntries, confirmCheckoutWithWorksites, mintCheckoutRunId, rememberUndone, removeCheckoutStage, renderCheckoutSurface, restoreUndoneChange, undoAllCheckoutStage } from './checkout.js';
import { COMBINE_DIALOG, confirmCombineBroadcast, handleCombineAction, handleCombineStakePromptAction, openCombineGroupDialog, openPlotCombineChooser } from './combine-dialog.js';
import { IDLE_COMBINE_GROUPS, candidateCombineInfo, combineGroupKey, idleCombineGroups, groupHasCombinableRow, plotCombineCandidates } from './combine.js';
import { initCustomSelect, selectValues, setSelectValues, syncCustomSelect } from './customSelect.js';
import { alternativePowerSourceLabel, editorGroups, editorOccupant, editorSlotLabel, editorVisibleColumnCounts, renderSlotEditor, syncPickerFocusRings } from './editor.js';
import { esc, formatPlotLabel, num, terrainAffinityProfile } from './format.js';
import { closeHazard, isSoleSource, openHazard } from './hazard.js';
import { CHEVRON_CLOSE_ICON, rarityGemHtml, splinterIconHtml } from './icons.js';
import { CHECKOUT_VERIFY_TARGETS, clearOperationPollTimer, confirmOperationBroadcast, requestOperationPreview, verifyBroadcast } from './operations.js';
import { abortWorkerUpgradeOptions, EXPANDED_DATA_LOADED, EXPANDED_ROWS, EXPAND_ERRORS, loadPlotDetail, presentationForRow, requestWorkerUpgradeOptions, updateMountedRow, workerUpgradeOptionsState } from './rows.js';
import { CERES_STAGE, hasStagedWorksiteBuilds, mutateEditorSlot, stageForSlot, stagePricedRemovals, stageToast, stagedReleasedItemRefs, stagedIncomingAssetRefs, stagedPowerCoreRefs, unstageWorksiteBuild } from './staging.js';
import { S } from './state.js';
import { bindTerrainAffinityOverflow, terrainAffinityHtml } from './terrainAffinity.js';
import { bindCountdowns, unbindCountdowns } from './countdown.js';
import { STAGED_PLOT_LOCKS, dismissPlotLock, recheckPlotLock, togglePlotLock } from './plot-lock.js';

const RENDER_ERROR_LOGGED_DEEDS = new Set();

// a signed deed lock's game verdict (by id) redraws its row and Changes; a refusal says why.
window.addEventListener?.('ceres:plot-lock-verdict', (event) => {
  const { uid, status, message } = event.detail || {};
  if (status === 'failed') stageToast(`Deed lock refused by the game: ${message}`, true);
  if (uid) { try { updateMountedRow(uid); } catch (_) { /* row not mounted */ } }
  try { renderCheckoutSurface(); } catch (_) { /* Changes not mounted */ }
});

export function stageDeltaToastText(delta) {
  return signedPp(delta);
}

const PICKER_VIEW_STORAGE_PREFIX = 'ceres:picker-view:v1:';
function pickerViewStorageKey(account) {
  return account ? `${PICKER_VIEW_STORAGE_PREFIX}${String(account).toLowerCase()}` : null;
}
export function readPickerViewPreference() {
  const key = pickerViewStorageKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return 'list';
  try {
    return localStorage.getItem(key) === 'grid' ? 'grid' : 'list';
  } catch (error) {
    return 'list';
  }
}
export function savePickerViewPreference(value) {
  const view = value === 'grid' ? 'grid' : 'list';
  const key = pickerViewStorageKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return;
  try { localStorage.setItem(key, view); } catch (error) { /* Presentation preference is optional. */ }
}

function mobilePickerHost(deedUid) {
  const sheet = document.getElementById('mobile-plot-sheet');
  if (document.documentElement.classList?.contains('ceres-lw-le-1279') !== true || sheet?.hidden || sheet?.dataset.deedUid !== deedUid) return null;
  return document.getElementById('mobile-plot-sheet-body');
}

function pickerPlotHost(deedUid) {
  return mobilePickerHost(deedUid) || document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(deedUid)}"]`);
}

function pickerElementFor(deedUid) {
  return pickerPlotHost(deedUid)?.querySelector('.inline-picker[data-picker="1"]') || null;
}
import { scrollPlotAfterSlotAdd, scrollPlotUnderHeader as scrollRowUnderHeader } from './table.js';
import { acquirePlotViewportLease, cancelPlotViewportLease, plotRowScrollGoal, plotViewportLeaseIsCurrent, releasePlotViewportLease } from './table.js';
import { TC_SWAP_COST, WORKSITE_MODAL_SELECTED, closeWorksiteModal, confirmWorksiteBroadcast, openWorksiteCancelDialog, openWorksiteModal, renderWorksiteModalBody, renderWorksiteVerdict, requestWorksiteCompletionPreview, requestWorksitePreview, selectWorksiteTile, stageThisWorksiteBuild, verifyWorksiteBroadcast, worksiteStageOnly } from './worksite.js';

// Dropdown-unification batch (2026-08-25, the owner: "we have inconsistency of
// dropdown behavior, JS script for main table, default behavior for worker
// table filters") -- the inline candidate picker's own filter bar
// (renderInlineCandidatePicker() -- Rarity/Splinter/Edition/Source selects)
// emitted RAW <select> markup and never went through customSelect.js, so it
// rendered with plain browser chrome while the main table's filters used the
// styled component. This wraps every `[data-picker-filter]` <select> found
// inside `scopeEl` the SAME way, so behaviour (open/keyboard/select/close
// click-outside) is identical everywhere, not just lookalike styling.
// Unlike initFilterDropdowns() above, this is called on every re-render, not
// once at boot: the picker's own filter bar is rebuilt from scratch (fresh
// DOM, via innerHTML) on every openPicker()/updateMountedRow() that mounts
// this row, because it lives inside renderSlotEditor()'s output, which those
// call sites regenerate wholesale rather than patch. initCustomSelect() is
// idempotent per-element (`selectEl._csel` guard) but that guard never
// matters here -- a freshly-created <select> from innerHTML is never the
// same node as any previous one, so it always (correctly) re-initialises.
// This does NOT leak listeners across those repeated re-renders: see
// customSelect.js's own 2026-08-25 comment on `_wireDocumentOutsideClick()`
// the outside-click handling was moved off a per-instance
// `document.addEventListener` for exactly this reason.
// Init, never sync: by the time this scans the DOM, the <select>'s <option>s
// were already baked into the innerHTML string in the SAME render pass (see
// renderInlineCandidatePicker() -- editionOptions/rarity/splinter/bucket
// option lists are all string-interpolated into the <select> markup itself
// not appended afterward). That is the opposite order of the item-34 "blank
// trigger" bug (`#sort-select` was init()'d empty, then had <option>s
// appended later) -- here every <select> already has its final options the
// first time initCustomSelect() ever sees it, so there is no populate-after
// init window for the trigger to render blank in. Guarded by a regression
// test (test_picker_filter_dropdown_not_blank_after_dynamic_edition_list).
// Rarity/Splinter get the SAME gem/splinter-icon decoration the main table's
// own Rarity filter uses (initFilterDropdowns() above) -- "one dropdown
// behaviour everywhere" means look-and-feel too, not just open/close
// mechanics. Edition/Source have no equivalent existing icon helper, so they
// render as plain text, same as the main table's Sort control.
const PICKER_FILTER_DECORATORS = {
  rarity: (v) => (v ? { icon: rarityGemHtml(v) } : null),
  splinter: (v) => (v ? { icon: splinterIconHtml(v) } : null),
};
// the sort group's pipe separates it from the filters on the same line; when the
// row wraps and the group starts a line, the pipe would stand alone, so it is dropped.
function syncSortDivider(scopeEl) {
  const sort = scopeEl.querySelector('.picker-bar .picker-sort-chips');
  const prev = sort?.previousElementSibling || sort?.parentElement?.previousElementSibling; // chips sit in the title row's end group; the pipe compares with the title
  if (!sort) return;
  const check = () => {
    const mid = (el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; };
    sort.classList.toggle('is-line-start', !prev || Math.abs(mid(prev) - mid(sort)) > 6);
  };
  check();
  if (typeof ResizeObserver === 'function' && !sort._sortDividerObserver) {
    sort._sortDividerObserver = new ResizeObserver(check);
    sort._sortDividerObserver.observe(sort.parentElement);
  }
}

function pickerGridTrackCount(grid) {
  const template = getComputedStyle(grid).gridTemplateColumns.trim();
  if (!template || template === 'none') return 0;
  const repeated = template.match(/^repeat\(\s*(\d+)\s*,/);
  if (repeated) return Number(repeated[1]);
  let depth = 0;
  let count = 0;
  let inTrack = false;
  for (const character of template) {
    if (/\s/.test(character) && depth === 0) {
      inTrack = false;
      continue;
    }
    if (!inTrack) { count += 1; inTrack = true; }
    if (character === '(') depth += 1;
    else if (character === ')') depth = Math.max(0, depth - 1);
  }
  return count;
}

function trimPickerGridLoadingRow(scopeEl) {
  const grid = scopeEl?.matches?.('.candidate-list.is-card-grid')
    ? scopeEl : scopeEl?.querySelector?.('.candidate-list.is-card-grid');
  if (!grid) return;
  const ghosts = [...grid.querySelectorAll('.candidate-grid-card.is-ghost')];
  if (!ghosts.length) return;
  const trackCount = pickerGridTrackCount(grid);
  if (trackCount > 0) ghosts.slice(trackCount).forEach((ghost) => ghost.remove());
}

export function initPickerFilterDropdowns(scopeEl) {
  if (!scopeEl) return;
  bindTerrainAffinityOverflow(scopeEl);
  syncSortDivider(scopeEl);
  scopeEl.querySelectorAll('select[data-picker-filter]').forEach((el) => {
    const decorateOption = PICKER_FILTER_DECORATORS[el.dataset.pickerFilter];
    // each rendered <select> already carries its own category `aria-label`
    // (candidates.js's renderInlineCandidatePicker(), same "Any <category>" pattern
    // the board filter row uses) -- pass it through to the compact trigger the same
    // way board.js's initFilterDropdowns() does with its own `ariaLabel` option.
    const ariaLabel = el.getAttribute('aria-label') || undefined;
    initCustomSelect(el, { ...(decorateOption ? { decorateOption } : {}), ...(ariaLabel ? { ariaLabel } : {}),
      ...(el.dataset.pickerFilter === 'ability' ? { multiple: true, emptyLabel: 'Ability', pluralNoun: 'abilities' } : {}) });
  });
  syncPickerFocusRings(scopeEl);
  trimPickerGridLoadingRow(scopeEl);
}

// mirrors board.js's clearBoardFilters() -- resets each filter control's OWN
// value/checked state and dispatches its OWN normal input/change event (never a
// private reset path), so every existing listener (handlePickerFilter, customSelect's
// trigger resync) reacts exactly as if the maintainer had cleared each control by hand.
// Sort and the worker's splinter-icon terrain-affinity badges are untouched -- the
// splinter <select> is only ever present for Runi/legacy pickers, never worker.
function clearPickerFilters(pickerElement) {
  if (!pickerElement) return;
  if (S.CERES_PICKER?.filters.bloodline_pairs && !pickerElement.querySelector('[data-picker-filter="bloodline_pairs"]')) {
    S.CERES_PICKER.filters.bloodline_pairs = false;
    releasePickerOverride('bloodline_pairs');
    seedSplinterSelection(S.CERES_PICKER.filters, S.CERES_PICKER.row);
    savePickerMemory();
    clearTimeout(S.CERES_PICKER.filterTimer);
    S.CERES_PICKER.filterTimer = setTimeout(() => fetchPickerPage(true), 180);
  }
  const reset = (selector, value) => {
    const el = pickerElement.querySelector(selector);
    if (!el) return;
    if (el.tagName === 'SELECT') {
      if (el.multiple) setSelectValues(el, []);
      else {
        if (el.value === value) return;
        el.value = value;
      }
      el.dispatchEvent(new Event('change', { bubbles: true }));
      syncCustomSelect(el);
    } else if (el.type === 'checkbox') {
      if (el.checked === value) return;
      el.checked = value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      if (el.value === value) return;
      el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };
  reset('[data-picker-filter="q"]', '');
  reset('[data-picker-filter="rarity"]', '');
  reset('[data-picker-filter="splinter"]', '');
  reset('[data-picker-filter="ability"]', '');
  reset('[data-picker-filter="edition"]', '');
  reset('[data-picker-filter="min_pp"]', '');
  reset('[data-picker-filter="max_pp"]', '');
  reset('[data-picker-filter="ready_now"]', false);
  reset('[data-picker-filter="include_on_land"]', false);
  reset('[data-picker-filter="land_only"]', false);
  reset('[data-picker-filter="bloodline_pairs"]', false);
  syncPickerFilterChrome(pickerElement);
}

// shows/hides the picker's own "Clear filters" button to match current
// filter state -- called after any real edit (handlePickerFilter) and after
// updatePickerRegion() patches the list, same pattern as board.js's own
// syncBoardFilterChrome().
export function syncPickerFilterChrome(pickerElement) {
  const btn = pickerElement?.querySelector('.picker-clear-filters');
  if (btn) btn.hidden = !pickerFiltersActive(S.CERES_PICKER);
}

export async function fetchPickerPage(reset = false) {
  const picker = S.CERES_PICKER;
  if (!picker || (picker.loading && !reset)) return;
  if (picker.groupId === 'worker') {
    const row = BOARD?.plots?.find((plot) => plot.deed_uid === picker.deedUid) || picker.row;
    picker.bloodlineSubTypes = [...new Set([...bloodlinePlotSubTypes(row), ...stagedBloodlineSubTypes(picker.deedUid), ...(picker.extraBloodlines || [])])];
    if (picker.filters.bloodline_pairs && !picker.bloodlineSubTypes.length) {
      picker.filters.bloodline_pairs = false;
      releasePickerOverride('bloodline_pairs');
      seedSplinterSelection(picker.filters, row);
    }
  }
  // `generation` used to be read from `picker.generation` without ever
  // being bumped here, so the staleness check below compared the picker's
  // generation field against itself -- always equal, never able to reject a
  // response. Every call now claims a fresh generation up front and writes it
  // onto the picker immediately, so an EARLIER in-flight call's response finds
  // `picker.generation` has moved on and is dropped as stale, no matter which
  // one resolves first.
  let editionReset = false;
  const generation = ++CERES_PICKER_GENERATION;
  picker.generation = generation;
  if (reset) { picker.offset = 0; picker.candidates = []; picker.total = null; picker.excludedCounts = {}; picker.rarityGroups = {}; picker.notices = []; picker.selectedKey = null; picker.detailKey = null; picker.detailPinned = false; picker.cardsShownAt = null; }
  // "Load 25 more" appends -- the rows already shown (and their decoded art) stay.
  const appendFrom = reset ? 0 : picker.candidates.length;
  picker.loading = true;
  picker.error = null;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === picker.deedUid);
  if (row) picker.row = row;
  if (row && !updatePickerRegion(row.deed_uid, appendFrom ? { footerOnly: true } : {})) updateMountedRow(row.deed_uid);
  try {
    const response = await fetch(playerQueryAppend(candidateQuery(picker, picker.offset)));
    if (!response.ok) throw new Error(`Candidate request failed: HTTP ${response.status}`);
    const payload = await response.json();
    if (S.CERES_PICKER !== picker || picker.generation !== generation) return;
    picker.candidates = reset ? payload.candidates || [] : picker.candidates.concat(payload.candidates || []);
    picker.total = Number(payload.total ?? payload.pool_size ?? 0) + picker.candidates.filter(candidate => candidate.planned).length;
    picker.source = payload.source || '';
    picker.excludedCounts = payload.excluded_counts || {};
    picker.rarityGroups = payload.rarity_groups || {};
    if (Array.isArray(payload.available_editions)) {
      picker.availableEditions = payload.available_editions.filter((id) => Number.isInteger(id) && id >= 0);
      if (picker.filters.edition && !picker.availableEditions.includes(Number(picker.filters.edition))) {
        picker.filters.edition = '';
        editionReset = true;
      }
      const editionSelect = pickerElementFor(picker.deedUid)?.querySelector('[data-picker-filter="edition"]');
      if (editionSelect) {
        editionSelect.innerHTML = pickerEditionOptionsHtml(picker);
        syncCustomSelect(editionSelect);
      }
    }
    picker.notices = payload.notices || [];
    picker.offset = picker.candidates.filter(candidate => !candidate.planned).length;
  } catch (error) {
    if (S.CERES_PICKER !== picker || picker.generation !== generation) return;
    picker.error = String(error);
  } finally {
    if (S.CERES_PICKER !== picker || picker.generation !== generation) return;
    picker.loading = false;
    if (editionReset && S.CERES_PICKER === picker && picker.generation === generation) {
      fetchPickerPage(true);
      return;
    }
    // a Runi, Totem or Title picker opened by the fill run with nothing to stake is skipped.
    if (S.CERES_PICKER === picker && picker.generation === generation && row && reset && picker.chain && !picker.error
        && picker.total === 0 && ['runi', 'totem', 'title'].includes(picker.groupId)) {
      advanceFillChain(row, picker.groupId, picker.slotIndex, { noRuni: picker.groupId === 'runi' })
        .then((moved) => { if (!moved && S.CERES_PICKER === picker) closePickerAfterFill(row); });
      return;
    }
    if (S.CERES_PICKER === picker && picker.generation === generation && row) {
      const apply = () => {
        if (S.CERES_PICKER !== picker || picker.generation !== generation) return;
        if (!updatePickerRegion(row.deed_uid, appendFrom && !picker.error ? { appendFrom } : {})) updateMountedRow(row.deed_uid);
        // the jump ends -- the held height is released and the new cards fade up.
        if (S.CERES_PICKER === picker && picker.advancing) {
          picker.advancing = false;
          const list = pickerPlotHost(row.deed_uid)?.querySelector('.candidate-list');
          if (list) { list.style.minHeight = ''; list.classList.remove('is-advancing'); list.classList.add('is-arriving'); }
        }
      };
      // drawing 25 cards stalls the page for a moment -- while the opening scroll runs, the cards wait
      // for it to finish (placeholders stay), so the glide never stutters.
      if (picker.scrolling) picker.afterScroll = apply; else apply();
    }
  }
}

let CERES_PICKER_GENERATION = 0;

// `filterOverrides` (optional) seeds the initial filters object -- e.g. the
// bloodline-companion affordance below opens straight into `bloodline_pairs
// true` rather than opening with the default filters and mutating them a tick
// later, which used to race two `fetchPickerPage(true)` calls against each
// other (the request already in flight before the mutation reflected the OLD
// unfiltered query, and whichever response landed last silently won -- fixed
// by 's generation bump in fetchPickerPage(), but seeding the filters
// up front avoids issuing the doomed first request at all).
// the picker's chips, selects, PP bounds and
// sort choice are kept per account and per group kind in this browser, like the sort choice () and the region
// () -- a view preference, never game data ( untouched). The name search is per lookup and never kept.
// A picker opened with explicit `filterOverrides` (bloodline pair flows, ) applies them on top of the memory
// an overridden key is not written back until the user changes it in that picker.
// the splinter icons are no longer remembered one by one -- `splinter_groups` holds the terrain groups
// (bonus / neutral / penalised) the maintainer last chose as a whole; each plot derives its own splinters from them.
// The Neutral card splinter is a separate preference from terrain-neutral groups.
const PICKER_MEMORY_KEYS = ['rarity', 'edition', 'ability', 'min_pp', 'max_pp', 'bucket', 'ready_now', 'include_on_land', 'land_only', 'bloodline_pairs', 'splinter_groups', 'neutral_splinter', 'keep_open', 'show_upgrade'];

// (his answers 2026-09-14): "neutral" = no terrain effect, and an unknown modifier counts as neutral.
const SPLINTER_GROUP_OF_STATE = { favoured: 'bonus', unchanged: 'neutral', unknown: 'neutral', penalised: 'penalised' };
const DEFAULT_SPLINTER_GROUPS = ['bonus', 'neutral'];
function plotSplinterGroups(row) {
  const groups = { bonus: [], neutral: [], penalised: [] };
  terrainAffinityProfile(row).peers.forEach((peer) => groups[SPLINTER_GROUP_OF_STATE[peer.state] || 'neutral'].push(String(peer.splinter).toLowerCase()));
  return groups;
}
function rememberedSplinterGroups(value) {
  return Array.isArray(value) ? value.filter((group) => DEFAULT_SPLINTER_GROUPS.includes(group) || group === 'penalised') : DEFAULT_SPLINTER_GROUPS;
}
function chosenSplinters(picker) { return String(picker.filters.splinter || '').toLowerCase().split(',').filter(Boolean); }
// The plot's own splinters for the remembered groups; none on this plot -> its neutral splinters, this plot only.
function seedSplinterSelection(filters, row) {
  if (filters.bloodline_pairs) { filters.splinter = ''; filters.include_terrain_penalised = true; return; } // his answer: pairs show all splinters
  const byGroup = plotSplinterGroups(row);
  let chosen = rememberedSplinterGroups(filters.splinter_groups).flatMap((group) => byGroup[group] || []);
  if (!chosen.length) chosen = byGroup.neutral;
  if (filters.neutral_splinter !== false) chosen.push('neutral');
  filters.splinter = chosen.join(',');
  filters.include_terrain_penalised = chosen.some((s) => byGroup.penalised.includes(s));
}
// A choice is remembered only when every terrain group on this plot is all on or all off; a partial group is this
// plot's own choice and the maintainer's default stays. Groups this plot does not have keep their remembered state.
function applySplinterSelection(picker, next) {
  const byGroup = plotSplinterGroups(picker.row);
  picker.filters.splinter = next.join(',');
  picker.filters.neutral_splinter = next.includes('neutral');
  picker.filters.include_terrain_penalised = next.some((s) => byGroup.penalised.includes(s));
  const whole = next.length > 0 && Object.values(byGroup).every((list) => {
    const on = list.filter((s) => next.includes(s)).length;
    return on === 0 || on === list.length;
  });
  if (whole) {
    const previous = rememberedSplinterGroups(picker.filters.splinter_groups);
    picker.filters.splinter_groups = Object.keys(byGroup).filter((group) => (byGroup[group].length ? byGroup[group].every((s) => next.includes(s)) : previous.includes(group)));
  }
  releasePickerOverride('splinter_groups');
  savePickerMemory();
}
const PICKER_SORTS = { worker: ['construction_pp', 'name'], runi: ['construction_pp', 'name'], totem: ['boost', 'name'], title: ['boost', 'name'] };
function pickerMemoryKey(account) { return account ? `ceres:picker-filters:v1:${String(account).toLowerCase()}` : null; }
function readPickerMemory(groupId) {
  const key = pickerMemoryKey(CURRENT_PLAYER || BOARD?.player);
  if (!key) return null;
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem(key) || 'null'); } catch (error) { stored = null; }
  const entry = stored && typeof stored === 'object' ? stored[groupId] : null;
  if (!entry || typeof entry !== 'object' || !entry.filters || typeof entry.filters !== 'object') return null;
  const filters = {};
  for (const name of PICKER_MEMORY_KEYS) if (name in entry.filters) filters[name] = entry.filters[name];
  const sort = PICKER_SORTS[groupId]?.includes(entry.sort) ? entry.sort : null;
  return { filters, sort };
}
export function savePickerMemory() {
  const picker = S.CERES_PICKER;
  const key = picker ? pickerMemoryKey(CURRENT_PLAYER || BOARD?.player) : null;
  if (!key) return;
  const held = picker.overrideKeys || new Set();
  const previous = readPickerMemory(picker.groupId)?.filters || {};
  const filters = {};
  for (const name of PICKER_MEMORY_KEYS) {
    const value = held.has(name) ? previous[name] : picker.filters[name];
    if (value !== undefined) filters[name] = value;
  }
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (error) { stored = {}; }
  if (typeof stored !== 'object') stored = {};
  stored[picker.groupId] = { filters, sort: picker.sort };
  try { localStorage.setItem(key, JSON.stringify(stored)); } catch (error) { /* a view preference is never worth an error */ }
}
function releasePickerOverride(name) { S.CERES_PICKER?.overrideKeys?.delete(name); }

// a Toil and Kin card staged onto the plot counts as a holder too (staked ones come from row.workers).
function stagedBloodlineSubTypes(deedUid) {
  return CERES_STAGE.filter((entry) => entry.deed_uid === deedUid && (entry.action === 'stake' || entry.action === 'replace'))
    .flatMap((entry) => (entry._asset?.land_abilities || []).filter((ability) => Array.isArray(ability) && ability[0] === 'BLOODLINE' && ability[2]).map((ability) => String(ability[2])));
}

// `opts.advance` (): a jump from the fill run -- no push-open, no scroll, the list keeps its height and fades.
// `opts.chain` (): this picker was opened by the fill run and continues it.
function openPicker(row, group, index, mode, filterOverrides, extraBloodlines = [], opts = {}) {
  const mobile = Boolean(mobilePickerHost(row.deed_uid));
  const heldList = opts.advance ? pickerPlotHost(row.deed_uid)?.querySelector('.candidate-list') : null;
  const heldHeight = heldList ? heldList.offsetHeight : 0;
  if (opts.advance) holdFillRunHeight(); else releaseFillRunHeight();
  if (!opts.advance && !mobile) reservePickerRoom(row.deed_uid); // room for the plot to reach the top
  const occupant = group.slots[index];
  const workerLike = group.id === 'worker' || group.id === 'runi';
  const itemLike = group.id === 'totem' || group.id === 'title';
  const remembered = readPickerMemory(group.id);
  const filters = { show_upgrade: false, q: '', rarity: '', splinter: '', edition: '', ability: [], min_pp: '', max_pp: '', bucket: 'all', ready_now: false, include_on_land: false, include_terrain_penalised: false, bloodline_pairs: false, ...(remembered?.filters || {}), ...(filterOverrides || {}) };
  const overrideKeys = new Set(Object.keys(filterOverrides || {}));
  // never open with both Land Cards and Bloodline pairs on; the one just asked for wins, else Bloodline pairs.
  if (filters.land_only && filters.bloodline_pairs) {
    if (overrideKeys.has('land_only') && !overrideKeys.has('bloodline_pairs')) filters.bloodline_pairs = false;
    else filters.land_only = false;
  }
  const bloodlineSubTypes = [...new Set([...bloodlinePlotSubTypes(row), ...stagedBloodlineSubTypes(row.deed_uid), ...extraBloodlines.filter(Boolean)])];
  if (!bloodlineSubTypes.length) filters.bloodline_pairs = false;
  if (group.id === 'worker') seedSplinterSelection(filters, row);
  const defaultSort = workerLike ? 'construction_pp' : itemLike ? 'boost' : 'net_gain';
  const ringKey = `${row.deed_uid}\u001f${group.id}\u001f${index}`;
  const previousPicker = S.CERES_PICKER;
  if (previousPicker && previousPicker.deedUid !== row.deed_uid) abortWorkerUpgradeOptions(previousPicker.deedUid);
  // the scroll position from before the picker opened; another slot of the same plot keeps it
  const savedScroll = previousPicker?.deedUid === row.deed_uid && Number.isFinite(previousPicker.savedScroll) ? previousPicker.savedScroll : window.scrollY;
  const ringStartedAt = previousPicker?.ringKey === ringKey && Number.isFinite(previousPicker.ringStartedAt)
    ? previousPicker.ringStartedAt : (typeof performance !== 'undefined' ? performance.now() : Date.now());
  S.CERES_PICKER = { row, deedUid: row.deed_uid, groupId: group.id, groupLabel: group.label, slotIndex: index, ringKey, ringStartedAt, mode, currentOccupant: occupant, generation: ++CERES_PICKER_GENERATION, preset: 'net_gain_default', sort: remembered?.sort || defaultSort, viewMode: mobile ? 'grid' : readPickerViewPreference(), mobile, selectedKey: null, detailKey: null, detailPinned: false, overrideKeys, targetTerrain: row.terrain || null, bloodlineSubTypes, extraBloodlines: extraBloodlines.filter(Boolean), offset: 0, total: null, source: '', excludedCounts: {}, rarityGroups: {}, notices: [], candidates: [], loading: true, error: null, upgradeOptionsDemand: true, filters, openedAt: opts.advance ? 0 : performance.now(), chain: Boolean(opts.chain), advancing: Boolean(opts.advance), savedScroll };
  loadOccupantUpgrade(S.CERES_PICKER);
  updateMountedRow(row.deed_uid);
  // one selection at a time -- the plot that held the picker before is redrawn
  // without it, so its selected/dimmed tiles and its open picker clear.
  if (previousPicker && previousPicker.deedUid !== row.deed_uid) updateMountedRow(previousPicker.deedUid);
  if (group.id === 'worker') demandWorkerUpgradeOptions(row, S.CERES_PICKER.generation);
  updatePickerRegion(row.deed_uid);
  if (opts.advance) {
    const list = pickerPlotHost(row.deed_uid)?.querySelector('.candidate-list');
    if (list) { if (heldHeight) list.style.minHeight = `${heldHeight}px`; list.classList.add('is-advancing'); }
    if (!mobile) scrollRowUnderHeader(row.deed_uid); // instant re-snap under the header
  }
  fetchPickerPage(true);
  if (!opts.advance && !mobile) scrollPlotUnderHeader(row.deed_uid); // the plot is already in place; a second scroll bounced
}

// "Fill next slot": the next empty worker slot (after this one, then from the start); with no worker
// slot left, the Runi picker; with no Runi to stake (`noRuni`), the best Power Core in one click (its tile is
// skipped); then Totem, then Title. A staged removal leaves its slot empty, so it counts. False = nothing left.
async function advanceFillChain(row, afterGroupId, afterIndex, { noRuni = false, chosenIndex = -1, wasStake = false } = {}) {
  const groups = editorGroups(row);
  const steps = ['worker', 'runi', 'power_core', 'totem', 'title'];
  if (afterGroupId === 'worker') {
    const workers = groups.find((item) => item.id === 'worker');
    const count = workers?.slots.length || 0;
    const next = Array.from({ length: count }, (_, step) => (afterIndex + 1 + step) % count).find((slot) => !workers.slots[slot]);
    if (workers?.actionable && next !== undefined) {
      if (wasStake && collapseToNextSlot(row, workers, next, chosenIndex)) return true;
      unbindCountdowns();
      openPicker(row, workers, next, 'stake', null, [], { advance: true, chain: true });
      return true;
    }
  }
  for (const id of steps.slice(steps.indexOf(afterGroupId) + 1)) {
    const group = groups.find((item) => item.id === id);
    const index = group?.actionable ? group.slots.findIndex((slot) => !slot) : -1;
    if (index < 0) continue;
    if (id === 'power_core') {
      const coreRemovalStaged = CERES_STAGE.some((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'power_core' && entry.action === 'unstake');
      if (noRuni && !coreRemovalStaged && !alternativePowerSourceLabel(row)) await stagePowerCoreOneClick(row, group, index);
      continue;
    }
    unbindCountdowns();
    openPicker(row, group, index, 'stake', null, [], { advance: true, chain: true });
    return true;
  }
  return false;
}

// an empty slot -> empty slot jump keeps the list as it is. The server's figures do not depend on which
// empty slot is filled (staging is never sent, only the reserved cards), so only the used card leaves -- it folds
// shut while the plot's tiles redraw around the list, which is lifted out and put back untouched.
function collapseToNextSlot(row, group, next, chosenIndex) {
  const picker = S.CERES_PICKER;
  const host = pickerPlotHost(row.deed_uid);
  const slice = host?.querySelector('.candidate-visible-slice');
  const card = chosenIndex >= 0 ? slice?.querySelector(`.candidate-card[data-candidate-index="${chosenIndex}"]`) : null;
  if (!picker || picker.deedUid !== row.deed_uid || !slice || !card) return false;
  picker.generation = ++CERES_PICKER_GENERATION;
  picker.loading = false;
  picker.candidates.splice(chosenIndex, 1);
  picker.total = Math.max(0, Number(picker.total || 0) - 1);
  picker.offset = picker.candidates.filter(candidate => !candidate.planned).length;
  picker.slotIndex = next;
  picker.mode = 'stake';
  picker.currentOccupant = null;
  picker.ringKey = `${row.deed_uid}${group.id}${next}`;
  picker.ringStartedAt = performance.now();
  picker.chain = true;
  holdFillRunHeight();
  slice.remove();
  updateMountedRow(row.deed_uid);
  const drawn = host.querySelector('.candidate-visible-slice');
  if (!drawn) {
    updatePickerRegion(row.deed_uid);
    if (!picker.mobile) scrollRowUnderHeader(row.deed_uid);
    return true;
  }
  drawn.replaceWith(slice);
  if (!picker.mobile) scrollRowUnderHeader(row.deed_uid); // instant; the plot stays right under the header
  const context = slice.querySelector('.picker-context');
  if (context) context.textContent = `Add · ${row.terrain || 'Plot'} · ${row.worksite_type || 'plot'} · ${editorSlotLabel(group, next)}`;
  // The cards after the used one move up one place in the list.
  card.querySelectorAll('[data-picker-action]').forEach((el) => el.removeAttribute('data-picker-action'));
  card.removeAttribute('data-picker-action');
  card.dataset.candidateIndex = '-1';
  slice.querySelectorAll('.candidate-list [data-candidate-index]').forEach((el) => {
    const index = Number(el.dataset.candidateIndex);
    if (index > chosenIndex) el.dataset.candidateIndex = String(index - 1);
  });
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) card.remove();
  else {
    card.style.height = `${card.offsetHeight}px`;
    card.classList.add('is-collapsing');
    void card.offsetHeight;
    card.style.height = '0px';
    card.addEventListener('transitionend', (event) => { if (event.propertyName === 'height') card.remove(); });
    setTimeout(() => card.remove(), 700);
    // the card taking the used card's place eases in.
    const successor = card.nextElementSibling?.classList.contains('candidate-card') ? card.nextElementSibling : null;
    if (successor) {
      successor.classList.add('is-settling');
      successor.addEventListener('animationend', () => successor.classList.remove('is-settling'), { once: true });
    }
  }
  updatePickerRegion(row.deed_uid, { footerOnly: true });
  return true;
}
// on a half-filled board the page is only tall enough for the plot to sit under the header while the long
// picker list is there. A jump's redraw shrank the page for a moment, the browser clamped the scroll and the plot
// dropped to the middle. The fill run holds the page height at what the current view needs and re-snaps the plot
// under the header; the hold is let go when the picker closes or a picker opens normally.
function holdFillRunHeight() {
  const need = Math.ceil(window.scrollY + window.innerHeight);
  if (need > (parseFloat(document.body.style.minHeight) || 0)) document.body.style.minHeight = `${need}px`;
}
function releaseFillRunHeight() { document.body.style.minHeight = ''; }

// as Echo Reapers'
// worksite picker does it (ui/worksite-actions.js, ): on open the scroll position is kept and the page gets room
// so even the last plot's row can reach the line under the sticky header (the cards are still loading while the plot
// travels); an explicit close (the picker's Close, or the open tile pressed again) scrolls back to where the user was
// the page keeping its height until the view is back, then letting it go. A choice still follows (the plot
// stays under the header; scrollPlotAfterSlotAdd). Phones (the plot sheet) do not scroll the board: see notes.
function reservePickerRoom(deedUid) {
  const goal = plotRowScrollGoal(deedUid);
  if (goal === null) return;
  const need = Math.ceil(goal + window.innerHeight);
  if (need > (parseFloat(document.body.style.minHeight) || 0)) document.body.style.minHeight = `${need}px`;
}
function closePickerRestoringScroll(row, picker) {
  const saved = picker.mobile ? null : picker.savedScroll;
  if (Number.isFinite(saved)) holdFillRunHeight(); else releaseFillRunHeight();
  unbindCountdowns();
  abortWorkerUpgradeOptions(row.deed_uid);
  S.CERES_PICKER = null;
  updateMountedRow(row.deed_uid);
  if (Number.isFinite(saved)) scrollWindowBack(saved, () => { if (!S.CERES_PICKER) releaseFillRunHeight(); });
}
// 300 ms ease-out like the open (table.js scrollPlotUnderHeader), under the same viewport lease so the user's own wheel
// key or touch stops it; instant with reduced motion.
function scrollWindowBack(top, done) {
  const to = Math.max(0, Math.round(top));
  const from = window.scrollY;
  const reduced = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (reduced || Math.abs(to - from) < 2) {
    cancelPlotViewportLease();
    if (Math.abs(to - from) > 0.5) window.scrollTo({ top: to, behavior: 'instant' });
    done();
    return;
  }
  const lease = acquirePlotViewportLease({ pickerRestore: true }, done);
  const start = performance.now();
  const step = (now) => {
    if (!plotViewportLeaseIsCurrent(lease)) return;
    const t = Math.min(1, (now - start) / 300);
    window.scrollTo({ top: from + (to - from) * (1 - Math.pow(1 - t, 3)), behavior: 'instant' });
    if (t < 1) requestAnimationFrame(step);
    else { releasePlotViewportLease(lease); done(); }
  };
  requestAnimationFrame(step);
}

function closeGridPickerAfterChoice(row, picker) {
  holdFillRunHeight();
  const finish = () => {
    if (S.CERES_PICKER !== picker) { releaseFillRunHeight(); return; }
    unbindCountdowns();
    abortWorkerUpgradeOptions(row.deed_uid);
    S.CERES_PICKER = null;
    updateMountedRow(row.deed_uid);
    if (typeof scrollPlotAfterSlotAdd === 'function') scrollPlotAfterSlotAdd(row.deed_uid);
    requestAnimationFrame(() => requestAnimationFrame(releaseFillRunHeight));
  };
  const panel = document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(row.deed_uid)}"] .inline-picker[data-picker="1"]`);
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (!panel || reduce) { finish(); return; }
  panel.classList.add('is-closing');
  setTimeout(finish, 220);
}

export function closeMobilePickerSelection(deedUid, { render = true } = {}) {
  const picker = S.CERES_PICKER?.deedUid === deedUid ? S.CERES_PICKER : null;
  const detail = S.MOBILE_SLOT_DETAIL?.deedUid === deedUid;
  if (!picker && !detail) return false;
  releaseFillRunHeight();
  unbindCountdowns();
  abortWorkerUpgradeOptions(deedUid);
  if (picker) S.CERES_PICKER = null;
  if (detail) S.MOBILE_SLOT_DETAIL = null;
  if (render) updateMountedRow(deedUid);
  return true;
}

function closePickerAfterFill(row) {
  releaseFillRunHeight();
  unbindCountdowns();
  abortWorkerUpgradeOptions(row.deed_uid);
  S.CERES_PICKER = null;
  updateMountedRow(row.deed_uid);
}

// the plot's table row lands just below the sticky header and the sticky column header. : through the one
// shared scroll (table.js scrollPlotUnderHeader, 300 ms ease-out, cancelled by the user's own input); the cards wait
// until it ends (see fetchPickerPage) and never longer than the safety below.
function scrollPlotUnderHeader(deedUid) {
  const picker = S.CERES_PICKER;
  if (!picker) return;
  picker.scrolling = true;
  const finish = () => {
    if (!picker.scrolling) return;
    picker.scrolling = false;
    const pending = picker.afterScroll; picker.afterScroll = null; pending?.();
  };
  const safety = setTimeout(finish, 2500);
  scrollRowUnderHeader(deedUid, { animate: true, owner: { picker: true, deedUid }, onDone: () => { clearTimeout(safety); finish(); } });
}

function demandWorkerUpgradeOptions(row, pickerGeneration) {
  const picker = S.CERES_PICKER;
  if (!picker || picker.deedUid !== row.deed_uid || picker.generation !== pickerGeneration) return;
  if (requestWorkerUpgradeOptions(row)) {
    picker.upgradeOptionsDemand = false;
    updatePickerRegion(row.deed_uid);
    return;
  }
  const frame = pickerPlotHost(row.deed_uid)?.querySelector('.editor-band');
  const state = frame?.dataset.plotState;
  if (state && !['ready', 'degraded', 'error'].includes(state)) {
    requestAnimationFrame(() => demandWorkerUpgradeOptions(row, pickerGeneration));
  } else {
    picker.upgradeOptionsDemand = false;
  }
}

// Item 17 -- one-click Power Core staking, no picker (see the empty-tile
// branch in renderEditorTile() for the fungibility reasoning). Hits the
// SAME `/candidates` endpoint the picker itself uses (server-authoritative
// respects cooldown/rental exclusions exactly like a picker selection would)
// and stages the top-ranked result -- this is the only asset family where
// "top-ranked" and "any available one" are the same thing, since every
// instance shares one `item_detail_id` (322) and a fixed 0 boost. Stages
// through the EXACT SAME `stageForSlot()`/`mutateEditorSlot()` path a picker
// selection uses, so it flows into CERES_STAGE identically and the
// portfolio checkout panel sees it exactly like any other staged move.
// Never broadcasts anything itself -- Review -> Sign -> Verify is still the
// only path to a signature, same as always.
let CERES_POWER_CORE_PENDING = false;

async function stagePowerCoreOneClick(row, group, index) {
  if (!group.actionable) return;
  const alternativeSource = alternativePowerSourceLabel(row);
  if (alternativeSource) {
    stageToast(`Power is already satisfied by ${alternativeSource}; no Power Core was staged.`);
    return;
  }
  if (CERES_POWER_CORE_PENDING) return;
  CERES_POWER_CORE_PENDING = true;
  const generation = BOARD_GENERATION;
  const player = CURRENT_PLAYER;
  const reserved = typeof stagedIncomingAssetRefs === 'function' ? stagedIncomingAssetRefs() : stagedPowerCoreRefs();
  const params = new URLSearchParams({ group_id: 'power_core', limit: '100', offset: '0', sort: 'net_gain', slot: String(index), bucket: 'all', preset: 'net_gain_default' });
  reserved.forEach((assetRef) => params.append('reserved_asset_ref', assetRef));
  stagedReleasedItemRefs('power_core', row.deed_uid).forEach((ref) => params.append('released_asset_ref', ref));
  try {
    const response = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(row.deed_uid)}/candidates?${params}`));
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !Array.isArray(payload.candidates)) {
      throw new Error(response.ok ? 'Power Core candidate response was malformed.' : `Power Core request failed: HTTP ${response.status}`);
    }
    if (BOARD_GENERATION !== generation || CURRENT_PLAYER !== player || BOARD?.plots.find((plot) => plot.deed_uid === row.deed_uid) !== row) {
      stageToast('The board changed while selecting a Power Core. Nothing was staged; try again.', true);
      return;
    }
    const rawCandidate = payload.candidates.find((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item) || typeof item.asset_ref !== 'string') return false;
      const assetRef = item.asset_ref.trim();
      return assetRef.length > 0 && !reserved.has(assetRef);
    }) || null;
    const candidate = rawCandidate ? { ...rawCandidate, asset_ref: rawCandidate.asset_ref.trim() } : null;
    if (!candidate) {
      stageToast('No unstaged Power Cores are left to stake right now.', true);
      updateMountedRow(row.deed_uid);
      return;
    }
    const currentGroup = editorGroups(row).find((item) => item.id === 'power_core');
    if (!currentGroup || currentGroup.slots[index]) {
      stageToast('The Power Core slot changed while selecting an asset. Nothing was staged; try again.', true);
      return;
    }
    const delta = candidateDelta(candidate, { currentOccupant: null });
    const incoming = editorOccupant(candidate, 'power_core');
    stageForSlot(row.deed_uid, currentGroup.id, index, 'stake', incoming.asset_ref, delta, null, incoming.name, candidate.source_deed_uid, candidate.base_pp_delta, candidate.cooldown, { ...incoming, plot_impact: candidate.plot_impact });
    mutateEditorSlot(row, currentGroup, index, incoming);
    // no success pill -- the Power Core tile and the plot's change badges already show it.
    updateMountedRow(row.deed_uid);
    if (!mobilePickerHost(row.deed_uid) && typeof scrollPlotAfterSlotAdd === 'function') scrollPlotAfterSlotAdd(row.deed_uid);
  } catch (error) {
    stageToast(`Could not stage a Power Core: ${error}`, true);
  } finally {
    CERES_POWER_CORE_PENDING = false;
  }
}

// Opens the worker picker pre-filtered to real Bloodline-pair candidates for
// this plot (`filters.bloodline_pairs`), shared by both places the "find a
// companion" affordance is offered: the SOLO badge (bloodlineCardBadgeHtml)
// and the pair-break removal warning's notice action. Pairing stays a FILTER
// never a scoring-priority term (the internal rules) -- this only changes which
// candidates are shown, never how they're ranked.
// The slot comes from the clicked tile; scanning for one can evict a card the user never selected.
// `subType` is the Bloodline the clicked button names. After the plot's own holder is
// unstaged, the plot has no Bloodline left to read -- the picker must still look for that one
// (a replacement holder or a companion), not claim there is nothing to match.
function openBloodlinePairPicker(row, slotIndex, subType = '') {
  const group = row && editorGroups(row).find((item) => item.id === 'worker');
  if (!row || !group || !group.actionable) return false;
  let index = slotIndex;
  let mode = 'stake';
  if (typeof slotIndex === 'number') {
    mode = group.slots[slotIndex] ? 'replace' : 'stake';
  } else if (slotIndex === null) {
    index = group.slots.findIndex((slot) => !slot);
  } else {
    return false;
  }
  if (index < 0) return false;
  openPicker(row, group, index, mode, { bloodline_pairs: true }, [subType]);
  return true;
}

function updateGridSelection(picker, index) {
  const candidate = picker.candidates[index];
  if (!candidate) return;
  const key = candidateStableKey(candidate, index);
  picker.selectedKey = key;
  const pickerElement = pickerElementFor(picker.deedUid);
  pickerElement?.querySelectorAll('.candidate-grid-card').forEach((card) => {
    const selected = card.dataset.candidateKey === key;
    card.classList.toggle('is-selected', selected);
    const control = card.querySelector('.candidate-grid-select');
    control?.setAttribute('aria-pressed', String(selected));
    control?.setAttribute('tabindex', selected ? '0' : '-1');
  });
}

function closeGridDetails(pickerElement, exceptCard = null) {
  pickerElement?.querySelectorAll('.candidate-grid-card').forEach((card) => {
    if (card === exceptCard) return;
    card.classList.remove('is-detail-open', 'is-detail-pinned');
    const panel = card.querySelector('.candidate-grid-detail');
    panel?.setAttribute('aria-hidden', 'true');
    card.querySelector('.candidate-detail-toggle')?.setAttribute('aria-expanded', 'false');
    card.querySelector('.candidate-detail-pin')?.setAttribute('aria-pressed', 'false');
  });
}

export function handlePickerKeydown(event) {
  const pickerElement = event.target.closest?.('.inline-picker[data-picker="1"]');
  if (!pickerElement || !S.CERES_PICKER || S.CERES_PICKER.viewMode !== 'grid') return;
  if (event.key === 'Escape') {
    const detailCard = event.target.closest('.candidate-grid-card');
    closeGridDetails(pickerElement);
    S.CERES_PICKER.detailKey = null;
    S.CERES_PICKER.detailPinned = false;
    detailCard?.querySelector('.candidate-detail-toggle')?.focus();
    return;
  }
  if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
  const current = event.target.closest('.candidate-grid-select:not([disabled])');
  if (!current) return;
  const controls = [...pickerElement.querySelectorAll('.candidate-grid-select:not([disabled])')];
  const at = controls.indexOf(current);
  if (at < 0) return;
  const columns = Math.max(1, Math.round(pickerElement.querySelector('.candidate-list')?.getBoundingClientRect().width / (current.closest('.candidate-grid-card')?.getBoundingClientRect().width || 1)) || 1);
  const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowDown' ? columns : -columns;
  const next = controls[at + delta];
  if (!next) return;
  event.preventDefault();
  current.setAttribute('tabindex', '-1');
  next.setAttribute('tabindex', '0');
  next.focus();
}

export async function handleSlotEditorClick(event) {
  if (await handleUpgradeAction(event)) return;
  const action = event.target.closest('[data-slot-action]');
  const checkoutAction = event.target.closest('[data-checkout-action]');
  const pickerAction = event.target.closest('[data-picker-action]');
  const combineAction = event.target.closest('[data-combine-action]');
  const hazardAction = event.target.closest('[data-hazard-action]');
  const opAction = event.target.closest('[data-op-action]');
  const noticeAction = event.target.closest('[data-notice-action]');
  const worksiteAction = event.target.closest('[data-worksite-action]');
  const combineStakeAction = event.target.closest('[data-combine-stake-action]');
  const plotLockAction = event.target.closest('[data-plot-lock-action]');
  if (!action && !checkoutAction && !pickerAction && !combineAction && !hazardAction && !opAction && !noticeAction && !worksiteAction && !combineStakeAction && !plotLockAction) return;
  event.preventDefault();
  event.stopPropagation();
  if (plotLockAction?.dataset.plotLockAction === 'toggle') {
    const row = BOARD?.plots.find(plot => plot.deed_uid === plotLockAction.dataset.deedUid);
    if (row && togglePlotLock(row)) { updateMountedRow(row.deed_uid); renderCheckoutSurface(); }
    else stageToast('The live deed lock state is unavailable or this change is already signed.', true);
    return;
  }
  if (action?.dataset.slotAction === 'retry-detail') { // "Try again" after 30 s unreachable
    const retryRow = BOARD?.plots.find((plot) => plot.deed_uid === action.dataset.deedUid);
    if (!retryRow) return;
    EXPANDED_DATA_LOADED.delete(retryRow.deed_uid);
    EXPAND_ERRORS.delete(retryRow.deed_uid);
    loadPlotDetail(retryRow);
    return;
  }
  if (event.target.closest('.editor-band[data-eos-detail-scaffold]')) return;
  if (combineStakeAction) { handleCombineStakePromptAction(combineStakeAction); return; }
  if (combineAction) { handleCombineAction(combineAction); return; }
  if (worksiteAction) {
    const worksiteActionKind = worksiteAction.dataset.worksiteAction;
    const overlay = document.getElementById('worksite-modal-overlay');
    const row = overlay ? BOARD?.plots.find((plot) => plot.deed_uid === overlay.dataset.deedUid) : null;
    if (worksiteActionKind === 'close') closeWorksiteModal();
    else if (worksiteActionKind === 'complete') {
      if (row?.worksite_completion) requestWorksiteCompletionPreview(row);
    }
    else if (worksiteActionKind === 'select-tile') {
      const target = worksiteAction.dataset.worksiteTarget;
      // Re-clicking the already-selected tile deselects it -- picking a target
      // is a real choice, not a one-way ratchet.
      const nextTarget = target === WORKSITE_MODAL_SELECTED ? null : target;
      // A preview built for the OLD target never sits under a new pick, and neither does a
      // verdict from a broadcast against it.
      S.WORKSITE_MODAL_PREVIEW = null;
      S.WORKSITE_MODAL_LAST_VERIFY = null;
      selectWorksiteTile(overlay, nextTarget);
      // one step -- picking a tile builds its preview at once; the footer's button signs.
      // a stage-only plot (H1 not yet live, but would pass once its own staged
      // power/workers confirm) never calls the server preview -- it always 400s until then.
      // The tile is just visually selected; the footer offers "Stage this build" instead.
      if (row && nextTarget && worksiteStageOnly(row)) renderWorksiteModalBody(row);
      else if (row && nextTarget) requestWorksitePreview(row, nextTarget, overlay.querySelector('.worksite-modal-tc input')?.checked ? TC_SWAP_COST : 0);
      else if (row) renderWorksiteModalBody(row);
    } else if (worksiteActionKind === 'confirm') {
      // Builds the real op preview for the selected target -- Phase 4.
      // Nothing broadcasts here; this only calls the server-side op builder and
      // shows what WOULD be signed. The actual signing is the review section's
      // own 'broadcast' button, below.
      if (row && WORKSITE_MODAL_SELECTED && !worksiteStageOnly(row)) {
        const tcChecked = !!overlay.querySelector('.worksite-modal-tc input')?.checked;
        requestWorksitePreview(row, WORKSITE_MODAL_SELECTED, tcChecked ? TC_SWAP_COST : 0);
      }
    } else if (worksiteAction.getAttribute('aria-disabled') === 'true') { // switched off -- tooltip only
      return;
    } else if (worksiteActionKind === 'stage') {
      if (row && WORKSITE_MODAL_SELECTED) stageThisWorksiteBuild(row, WORKSITE_MODAL_SELECTED);
    } else if (worksiteActionKind === 'stage-and-sign') { // this plot's staged power first, then the build
      if (row && WORKSITE_MODAL_SELECTED) {
        if (stageThisWorksiteBuild(row, WORKSITE_MODAL_SELECTED)) {
          const result = await confirmCheckoutWithWorksites({ deedUid: row.deed_uid });
          if (!result?.ok) stageToast(result?.reason || 'Could not start signing this worksite build.', true);
        }
      }
    } else if (worksiteActionKind === 'cancel-broadcast') {
      if (row && S.WORKSITE_MODAL_PREVIEW?.payload?.kind === 'worksite_cancel') confirmWorksiteBroadcast(row, worksiteAction);
    } else if (worksiteActionKind === 'broadcast') {
      if (row) confirmWorksiteBroadcast(row, worksiteAction);
      else {
        // item 3 -- BOARD.plots no longer holding this deed_uid (a
        // refresh or player switch raced the open modal) previously made this
        // click a silent no-op: no toast, nothing disabled, nothing signed
        // no explanation. Never silently drop a click on the sign button.
        const msg = 'This plot could not be found on the current board anymore. Close this dialog and reopen it before signing -- nothing was broadcast.';
        renderWorksiteVerdict(overlay, { tone: 'danger', title: 'Plot no longer on the board', detail: msg, retry: false });
      }
    } else if (worksiteActionKind === 'verify-again') {
      // Requirement 3 -- "not yet observable" gets its own re-check action
      // rather than forcing a full page reload to find out whether the
      // change has landed since the last check.
      if (S.WORKSITE_MODAL_LAST_VERIFY) verifyWorksiteBroadcast(S.WORKSITE_MODAL_LAST_VERIFY.deedUid, S.WORKSITE_MODAL_LAST_VERIFY.chunk, S.WORKSITE_MODAL_LAST_VERIFY.txId);
    }
    return;
  }
  if (opAction) {
    const target = opAction.dataset.previewScope === 'portfolio'
      ? { scope: 'portfolio' }
      : BOARD?.plots.find((plot) => plot.deed_uid === opAction.dataset.deedUid);
    if (target && opAction.dataset.opAction === 'confirm') {
      // with any staged worksite build present, the checkout's own portfolio Sign
      // button must still run power/workers first and only THEN reach the worksite step
      // never the plain card-move broadcast alone.
      if (target.scope === 'portfolio' && (hasStagedWorksiteBuilds() || [...STAGED_PLOT_LOCKS.values()].some(lock => !lock.txId && !lock.blockedReason) || constructionModel().signable.length)) confirmCheckoutWithWorksites(); // feeds too
      else confirmOperationBroadcast(target, { runId: mintCheckoutRunId(), runKind: 'plan' });
    }
    return;
  }
  if (noticeAction?.dataset.noticeAction === 'open-bloodline-pairs') {
    const row = BOARD?.plots.find((plot) => plot.deed_uid === noticeAction.closest('[data-deed-uid]')?.dataset.deedUid);
    const tile = noticeAction.closest('[data-slot-index]');
    const slotIndex = tile ? Number(tile.dataset.slotIndex) : null;
    if (!openBloodlinePairPicker(row, slotIndex, noticeAction.dataset.subType || '')) {
      stageToast('All worker slots are full — open a worker tile to swap in a Bloodline companion.', true);
    }
    return;
  }
  // defect 3 -- checkout verification's own "Check again": the button lives
  // inside the `.op-state` element verifyBroadcast() last wrote into, so that element's
  // CHECKOUT_VERIFY_TARGETS entry is the same {chunk, review, txId} verifyBroadcast()
  // needs to re-run the check -- never a fresh Keychain broadcast.
  if (noticeAction?.dataset.noticeAction === 'retry-verification') {
    const stateEl = noticeAction.closest('.op-state');
    const target = stateEl ? CHECKOUT_VERIFY_TARGETS.get(stateEl) : null;
    if (target?.txId) {
      clearOperationPollTimer(target.entryId);
      verifyBroadcast(target.chunk, stateEl, target.txId, target.review).catch(() => {});
    }
    return;
  }
  if (checkoutAction) {
    const checkoutType = checkoutAction.dataset.checkoutAction;
    // a signed deed lock the game has no record of is dismissed; one with no answer is checked again.
    if (checkoutType === 'lock-dismiss' || checkoutType === 'lock-recheck') {
      const uid = checkoutAction.dataset.deedUid;
      if (checkoutType === 'lock-dismiss' ? dismissPlotLock(uid) : recheckPlotLock(uid)) { updateMountedRow(uid); renderCheckoutSurface(); }
      return;
    }
    if (checkoutType === 'lock-undo') {
      const row = BOARD?.plots.find(plot => plot.deed_uid === checkoutAction.dataset.deedUid);
      if (!row) { stageToast('This plot is absent from the live board. Reload before changing its lock.', true); return; }
      try {
        if (!togglePlotLock(row)) throw new Error('This signed change cannot be undone.');
        updateMountedRow(row.deed_uid);
        renderCheckoutSurface();
      } catch (error) { stageToast(String(error?.message || error), true); }
      return;
    }
    if (checkoutType === 'toggle') {
      if (document.getElementById('checkout-btn')?.hidden) return; // the merged button also opens for cooling cards alone
      S.CERES_CHECKOUT_OPEN = !S.CERES_CHECKOUT_OPEN;
      renderCheckoutSurface();
      return;
    }
    if (checkoutType === 'copy-keychain-link') {
      try {
        await navigator.clipboard.writeText(window.location.href);
        stageToast('Page link copied. Open it in the Hive Keychain app browser.');
      } catch (error) {
        stageToast('Could not copy the page link. Copy it from the browser address bar.', true);
      }
      return;
    }
    if (checkoutType === 'prepare') {
      if (checkoutAction.disabled || !checkoutSignableEntries().length) {
        stageToast('Every staged move is set aside until it can be signed; see Changes.', true);
        return;
      }
      if (checkoutBlockedInFlightEntries().length) {
        stageToast(noticeMessage(catalogNotice('verification_in_flight_refusal')), true);
        return;
      }
      requestOperationPreview({ scope: 'portfolio' });
      return;
    }
    if (checkoutType === 'go' || checkoutType === 'go-source') { // jump to the change's tile
      const data = checkoutAction.dataset;
      revealStagedChange(checkoutType === 'go' ? { deedUid: data.deedUid, groupId: data.groupId, slotIndex: data.slotIndex } : { deedUid: data.deedUid, assetRef: data.assetRef });
      return;
    }
    if (checkoutType === 'undo-all') {
      await undoAllCheckoutStage();
      return;
    }
    if (checkoutType === 'undo-worksite') {
      const undone = await unstageWorksiteBuild(checkoutAction.dataset.deedUid);
      stageToast(undone
        ? 'Undone: the staged worksite build was removed.'
        : 'This change could not be undone because a previous wallet request is still being checked.', !undone);
      return;
    }
    if (checkoutType === 'sign-worksites') { // Standalone worksite builds and deed locks use the checkout signer.
      confirmCheckoutWithWorksites();
      return;
    }
    if (checkoutType === 'remove' || checkoutType === 'strip-undo') {
      const entry = CERES_STAGE.find((candidate) => candidate.deed_uid === checkoutAction.dataset.deedUid
        && candidate.group_id === checkoutAction.dataset.groupId
        && String(candidate.slot_index) === checkoutAction.dataset.slotIndex);
      if (entry && checkoutType === 'strip-undo') rememberUndone(entry); // the tile stays, dimmed, until clicked again
      if (entry) await removeCheckoutStage(entry);
      return;
    }
    if (checkoutType === 'restore-undone') {
      await restoreUndoneChange(checkoutAction.dataset.deedUid, checkoutAction.dataset.groupId, checkoutAction.dataset.slotIndex);
      return;
    }
  }
  if (hazardAction) {
    const overlay = document.getElementById('slot-hazard-overlay');
    if (overlay?.dataset.combine === '1') {
      if (hazardAction.dataset.hazardAction === 'close') closeHazard();
      else if (hazardAction.dataset.hazardAction === 'confirm') confirmCombineBroadcast();
      return;
    }
    if (hazardAction.dataset.hazardAction === 'close') { closeHazard(); return; }
    const row = BOARD?.plots.find((plot) => plot.deed_uid === overlay?.dataset.deedUid);
    if (row) {
      const group = editorGroups(row).find((item) => item.id === overlay.dataset.groupId);
      const index = Number(overlay.dataset.slotIndex);
      const occupant = group?.slots[index];
      if (group && occupant) await stagePricedRemovals(row, [{ group, index, occupant }], group.id === 'runi' ? 'Staged: sole Runi unstake.' : 'Staged: sole Power Core unstake.');
    }
    closeHazard();
    return;
  }
  if (pickerAction) {
    const picker = S.CERES_PICKER;
    if (!picker) return;
    const row = BOARD?.plots.find((plot) => plot.deed_uid === picker.deedUid);
    if (!row) return;
    if (pickerAction.dataset.pickerAction === 'close') {
      S.MOBILE_SLOT_DETAIL = null;
      // play the reverse push-out, then close exactly as before.
      const panel = pickerAction.closest('.inline-picker');
      const finish = () => { if (S.CERES_PICKER !== picker) return; closePickerRestoringScroll(row, picker); };
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      if (!panel || reduce || panel.classList.contains('is-closing')) { if (!panel?.classList.contains('is-closing')) finish(); return; }
      panel.classList.add('is-closing');
      setTimeout(finish, 220);
      return;
    }
    if (pickerAction.dataset.pickerAction === 'more') { fetchPickerPage(false); return; }
    // presentation only; retain candidates and avoid a new query.
    if (pickerAction.dataset.pickerAction === 'toggle-upgrade') {
      picker.filters.show_upgrade = !picker.filters.show_upgrade;
      releasePickerOverride('show_upgrade');
      savePickerMemory();
      updateMountedRow(row.deed_uid);
      return;
    }
    if (pickerAction.dataset.pickerAction === 'toggle-view') {
      if (picker.mobile) return;
      const next = picker.viewMode === 'grid' ? 'list' : 'grid';
      picker.viewMode = next;
      savePickerViewPreference(next);
      updateMountedRow(row.deed_uid);
      requestAnimationFrame(() => pickerPlotHost(row.deed_uid)?.querySelector('.picker-view-switch')?.focus());
      return;
    }
    if (pickerAction.dataset.pickerAction === 'select') {
      updateGridSelection(picker, Number(pickerAction.dataset.candidateIndex));
      return;
    }
    if (pickerAction.dataset.pickerAction === 'details') {
      const card = pickerAction.closest('.candidate-grid-card');
      const panel = card?.querySelector('.candidate-grid-detail');
      if (!card || !panel) return;
      closeGridDetails(pickerAction.closest('.inline-picker'), card);
      const open = card.classList.contains('is-detail-pinned') || !card.classList.contains('is-detail-open');
      card.classList.toggle('is-detail-open', open);
      panel.setAttribute('aria-hidden', String(!open));
      pickerAction.setAttribute('aria-expanded', String(open));
      picker.detailKey = open ? card.dataset.candidateKey || null : null;
      picker.detailPinned = card.classList.contains('is-detail-pinned');
      return;
    }
    if (pickerAction.dataset.pickerAction === 'pin-details') {
      const card = pickerAction.closest('.candidate-grid-card');
      const panel = card?.querySelector('.candidate-grid-detail');
      if (!card || !panel) return;
      closeGridDetails(pickerAction.closest('.inline-picker'), card);
      const pinned = !card.classList.contains('is-detail-pinned');
      card.classList.toggle('is-detail-pinned', pinned);
      card.classList.toggle('is-detail-open', pinned);
      panel.setAttribute('aria-hidden', String(!pinned));
      pickerAction.setAttribute('aria-pressed', String(pinned));
      const detailToggle = card.querySelector('.candidate-detail-toggle');
      detailToggle?.setAttribute('aria-expanded', String(pinned));
      picker.detailKey = pinned ? card.dataset.candidateKey || null : null;
      picker.detailPinned = pinned;
      if (!pinned) detailToggle?.focus();
      return;
    }
    if (pickerAction.dataset.pickerAction === 'sort') { picker.sort = pickerAction.dataset.sort; savePickerMemory(); fetchPickerPage(true); return; } // kept
    if (pickerAction.dataset.pickerAction === 'clear-picker-filters') {
      clearPickerFilters(pickerAction.closest('.inline-picker'));
      return;
    }
    // the bar's splinter icons are the Splinter filter. : several at once -- a click adds or removes
    // that splinter. : whole terrain groups are remembered (applySplinterSelection).
    if (pickerAction.dataset.pickerAction === 'splinter') {
      const value = String(pickerAction.dataset.splinter || '').toLowerCase();
      const chosen = chosenSplinters(picker);
      applySplinterSelection(picker, chosen.includes(value) ? chosen.filter((s) => s !== value) : [...chosen, value]);
      fetchPickerPage(true);
      return;
    }
    // Footer count chips: show what the current filters hide. : "show penalised" adds the penalised splinters.
    if (pickerAction.dataset.pickerAction === 'show-hidden-penalised') {
      const byGroup = plotSplinterGroups(picker.row);
      const base = chosenSplinters(picker).length ? chosenSplinters(picker) : [...byGroup.bonus, ...byGroup.neutral];
      applySplinterSelection(picker, [...new Set([...base, ...byGroup.penalised])]);
      fetchPickerPage(true);
      return;
    }
    if (pickerAction.dataset.pickerAction === 'show-hidden-not-ready') { picker.filters.ready_now = false; releasePickerOverride('ready_now'); savePickerMemory(); fetchPickerPage(true); return; }
    if (pickerAction.dataset.pickerAction === 'show-hidden-on-land') { picker.filters.include_on_land = true; releasePickerOverride('include_on_land'); savePickerMemory(); fetchPickerPage(true); return; }
    if (pickerAction.dataset.pickerAction === 'combine') {
      const candidate = picker.candidates[Number(pickerAction.dataset.candidateIndex)];
      const info = candidate ? candidateCombineInfo(candidate) : null;
      if (!info) return; // stale click target from a since-refetched candidate list
      IDLE_COMBINE_GROUPS.set(info.key, info.copies);
      openCombineGroupDialog(info.key);
      return;
    }
    if (pickerAction.dataset.pickerAction === 'choose') {
      const rarity = pickerAction.dataset.pickerRarity;
      const candidate = rarity
        ? picker.rarityGroups?.[rarity]?.candidate
        : picker.candidates[Number(pickerAction.dataset.candidateIndex)];
      if (!candidate || plannedPlacementReason(candidate, picker)) return;
      const group = editorGroups(row).find((item) => item.id === picker.groupId);
      const outgoing = picker.currentOccupant;
      const delta = candidateDelta(candidate, picker);
      const incoming = editorOccupant(candidate, group?.id || picker.groupId);
      if (group) {
        const elsewhere = CERES_STAGE.find((entry) => entry.action !== 'unstake' && String(entry.asset_ref) === String(incoming.asset_ref)
          && (entry.deed_uid !== row.deed_uid || entry.group_id !== group.id || String(entry.slot_index) !== String(picker.slotIndex)));
        if (elsewhere) {
          if (elsewhere.txId) { stageToast('This card or item has already been signed. Wait for its result.', true); return; }
          await removeCheckoutStage(elsewhere);
          if (CERES_STAGE.includes(elsewhere)) return;
        }
        if (stageForSlot(row.deed_uid, group.id, picker.slotIndex, picker.mode, incoming.asset_ref, delta, outgoing?.asset_ref || null, incoming.name, candidate.source_deed_uid, candidate.base_pp_delta, candidate.cooldown, { ...incoming, plot_impact: candidate.plot_impact }) === false) return;
        mutateEditorSlot(row, group, picker.slotIndex, incoming);
        const back = candidate.planned && chainReturn(chainPlans()[candidate.chain_id], row.deed_uid, picker.slotIndex);
        stageToast(candidate.planned
          ? `Staged: ${incoming.name} (level ${incoming.level}) ${back ? 'goes back to' : 'on'} Plot ${formatPlotLabel(row.plot)}, slot ${picker.slotIndex + 1} after the combine.`
          : `Staged: ${incoming.name} in ${editorSlotLabel(group, picker.slotIndex)} (${stageDeltaToastText(delta)} PP).`);
      }
      // "Fill next slot": stay open and move on to the next empty slot of the plot.
      if (group && (picker.filters.keep_open || picker.chain)) {
        if (await advanceFillChain(row, group.id, picker.slotIndex, { chosenIndex: rarity ? -1 : Number(pickerAction.dataset.candidateIndex), wasStake: picker.mode === 'stake' })) return;
        if (S.CERES_PICKER === picker) closePickerAfterFill(row);
        return;
      }
      if (picker.viewMode === 'grid') { closeGridPickerAfterChoice(row, picker); return; }
      releaseFillRunHeight();
      unbindCountdowns();
      abortWorkerUpgradeOptions(row.deed_uid);
      S.CERES_PICKER = null;
      updateMountedRow(row.deed_uid);
      if (!picker.mobile && typeof scrollPlotAfterSlotAdd === 'function') scrollPlotAfterSlotAdd(row.deed_uid);
      return;
    }
    return;
  }
  const deedUid = action.dataset.deedUid;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  if (!row) return;
  if (action.dataset.slotAction === 'cancel-construction') { openWorksiteCancelDialog(row); return; }
  if (action.dataset.slotAction === 'open-worksite-modal') { openWorksiteModal(row); return; }
  if (action.dataset.slotAction === 'mobile-details-close') {
    releaseFillRunHeight();
    unbindCountdowns();
    abortWorkerUpgradeOptions(deedUid);
    S.MOBILE_SLOT_DETAIL = null;
    S.CERES_PICKER = null;
    updateMountedRow(deedUid);
    return;
  }
  const group = editorGroups(row).find((item) => item.id === action.dataset.groupId);
  const index = Number(action.dataset.slotIndex);
  if (!group) return;
  if (action.dataset.slotAction === 'mobile-details') {
    S.MOBILE_SLOT_DETAIL = { deedUid, groupId: group.id, slotIndex: index };
    const pending = CERES_STAGE.find((entry) => entry.deed_uid === deedUid && entry.group_id === group.id && entry.slot_index === index);
    const runi = group.id === 'power_core' ? editorGroups(row).find((item) => item.id === 'runi') : null;
    if (!pending?.txId && runi?.actionable && !runi.slots[0]) openPicker(row, runi, 0, 'stake');
    else if (group.actionable && !pending?.txId && group.id !== 'power_core' && group.slots[index]) openPicker(row, group, index, 'replace');
    else { S.CERES_PICKER = null; updateMountedRow(deedUid); }
    return;
  }
  if (mobilePickerHost(deedUid) && ['remove', 'restore', 'undo-move'].includes(action.dataset.slotAction)) {
    S.MOBILE_SLOT_DETAIL = null;
    S.CERES_PICKER = null;
    unbindCountdowns();
    abortWorkerUpgradeOptions(deedUid);
  }
  if (action.dataset.slotAction === 'combine-plot') {
    const candidates = plotCombineCandidates(row, group);
    if (!candidates.length) return; // stale click target from a since-rerendered plot
    if (candidates.length === 1) {
      IDLE_COMBINE_GROUPS.set(candidates[0].key, candidates[0].copies);
      openCombineGroupDialog(candidates[0].key);
    } else {
      openPlotCombineChooser(row, candidates);
    }
    return;
  }
  if (action.dataset.slotAction === 'combine-tile') {
    const occupant = group.slots[index];
    if (!occupant || occupant.card_detail_id == null || !S.IDLE_CARDS_CACHE) return;
    const key = combineGroupKey(occupant);
    const copies = idleCombineGroups(S.IDLE_CARDS_CACHE).get(key);
    if (!copies || !groupHasCombinableRow(copies)) return;
    IDLE_COMBINE_GROUPS.set(key, copies);
    openCombineGroupDialog(key);
    // Tags the dialog so verifyCombineBroadcast() knows to offer the post-combine
    // stake choice once this specific combine is confirmed -- absent for every
    // other opener (worker-table door, Idle Cards table, the worker_upgrade
    // planning cycle), so only a tile-triggered combine ever asks.
    if (COMBINE_DIALOG) COMBINE_DIALOG.tileContext = { deedUid: row.deed_uid, groupId: group.id, slotIndex: index };
    return;
  }
  if (action.dataset.slotAction === 'add-runi') {
    const runi = editorGroups(row).find((item) => item.id === 'runi');
    const pending = CERES_STAGE.find((entry) => entry.deed_uid === deedUid && entry.group_id === group.id && entry.slot_index === index);
    if (runi?.actionable && !runi.slots[0] && !pending?.txId) openPicker(row, runi, 0, 'stake');
    return;
  }
  if (action.dataset.slotAction === 'add' || action.dataset.slotAction === 'replace') {
    // clicking the tile whose picker is already open deselects it -- same path as
    // the picker's own Close button.
    const open = S.CERES_PICKER;
    if (open && open.deedUid === row.deed_uid && open.groupId === group.id && open.slotIndex === index) {
      closePickerRestoringScroll(row, open); // ('s height hold is let go once the view is back)
      return;
    }
    if (group.actionable) openPicker(row, group, index, action.dataset.slotAction === 'replace' ? 'replace' : 'stake');
    return;
  }
  if (action.dataset.slotAction === 'add-power-core') {
    // while this plot's Power Core removal is staged, the empty core slot undoes that
    // removal (one click puts the core back and clears it from the checkout) instead of staging
    // a second core that the user then has to clear from the checkout.
    const stagedCoreRemoval = CERES_STAGE.find((entry) => entry.deed_uid === row.deed_uid
      && entry.group_id === group.id && entry.action === 'unstake');
    if (stagedCoreRemoval) { await removeCheckoutStage(stagedCoreRemoval); return; }
    stagePowerCoreOneClick(row, group, index);
    return;
  }
  if (action.dataset.slotAction === 'undo-move') { // undo a move from its source tile
    const d = action.dataset;
    const moving = CERES_STAGE.find((entry) => entry.deed_uid === d.moveDeedUid && entry.group_id === d.moveGroupId
      && String(entry.slot_index) === d.moveSlotIndex && !entry._synthetic && !entry.txId);
    if (moving) await removeCheckoutStage(moving);
    return;
  }
  if (action.dataset.slotAction === 'restore') {
    // Cancels a staged REMOVAL by putting the card back -- the mirror of the
    // v0.5.20 "click the staged tile to undo a staged ADD" flow. Deliberately
    // reuses removeCheckoutStage() unchanged: for an `unstake` entry it already
    // reprices the plot's remaining removals via the server endpoint
    // restores `entry._previous_asset` into the slot (undoing any bloodline-pair
    // break or zero-capacity warning the removal introduced, since the full
    // asset -- land_abilities/sole_holder_for included -- comes back), and never
    // touches the mixed-direction guard (this is an undo of the user's own
    // pending intent, not a new stage in the opposite direction).
    const stagedEntry = CERES_STAGE.find((entry) => entry.deed_uid === row.deed_uid
      && entry.group_id === group.id && entry.slot_index === index && entry.action === 'unstake');
    if (!stagedEntry) return;
    await removeCheckoutStage(stagedEntry);
    return;
  }
  if (action.dataset.slotAction === 'remove') {
    const stagedEntry = CERES_STAGE.find((entry) => entry.deed_uid === row.deed_uid
      && entry.group_id === group.id && entry.slot_index === index);
    if (stagedEntry) {
      await removeCheckoutStage(stagedEntry);
      return;
    }
    const occupant = group.slots[index];
    if (!occupant) return;
    if ((group.id === 'power_core' || group.id === 'runi') && isSoleSource(row, group.id)) { openHazard(row, group, index); return; }
    await stagePricedRemovals(
      row, [{ group, index, occupant }],
      group.id === 'power_core' ? 'Power Core removed; another source covers this plot. No cooldown.'
        : group.id === 'runi' ? 'Runi removed; another source covers this plot. No cooldown either way.'
        : `Staged: remove ${occupant.name}.`,
    );
    return;
  }
  if (action.dataset.slotAction === 'fill_plot') { openFillPlotModal(row); return; }
  if (['fill', 'clear', 'clear_plot'].includes(action.dataset.slotAction)) {
    if (action.dataset.slotAction === 'fill') { openPicker(row, group, group.slots.findIndex((slot) => !slot), 'stake'); return; }
    // a slot showing a STAGED incoming card (a staged add/move/replace onto this plot) is not
    // staked here, so pricing it as a removal fails ("not staked on deed"). Bulk Clear first undoes
    // those staged entries -- the same rule the single-slot remove uses -- then prices removals only
    // for cards actually staked on this plot, read fresh from the slots after the undo.
    const stagedIncomingEntry = (groupId, slotIndex) => CERES_STAGE.find((entry) => entry.deed_uid === row.deed_uid
      && entry.group_id === groupId && entry.slot_index === slotIndex && entry.action !== 'unstake');
    const clearGroups = action.dataset.slotAction === 'clear_plot'
      ? editorGroups(row).filter((itemGroup) => itemGroup.actionable).map((itemGroup) => itemGroup.id)
      : [group.id];
    const undoEntries = [];
    editorGroups(row).filter((itemGroup) => clearGroups.includes(itemGroup.id)).forEach((itemGroup) => {
      itemGroup.slots.forEach((occupant, slotIndex) => {
        const entry = occupant ? stagedIncomingEntry(itemGroup.id, slotIndex) : null;
        if (entry) undoEntries.push(entry);
      });
    });
    for (const entry of undoEntries) await removeCheckoutStage(entry); // sequential: each holds the plot's pricing lock
    const specs = [];
    editorGroups(row).filter((itemGroup) => clearGroups.includes(itemGroup.id)).forEach((itemGroup) => {
      itemGroup.slots.forEach((occupant, slotIndex) => {
        if (!occupant || stagedIncomingEntry(itemGroup.id, slotIndex)) return;
        specs.push({ group: itemGroup, index: slotIndex, occupant });
      });
    });
    const label = action.dataset.slotAction === 'clear_plot' ? 'Staged clear for the whole plot.' : `Staged clear for ${group.label}.`;
    if (specs.length) await stagePricedRemovals(row, specs, label);
    else if (undoEntries.length) stageToast(`Undid ${undoEntries.length} staged change${undoEntries.length === 1 ? '' : 's'}; nothing left to clear.`);
    return;
  }
}

export function handlePickerFilter(event) {
  const input = event.target.closest('[data-picker-filter]');
  if (!input || !S.CERES_PICKER) return;
  event.stopPropagation();
  const wasPairs = Boolean(S.CERES_PICKER.filters.bloodline_pairs);
  S.CERES_PICKER.filters[input.dataset.pickerFilter] = input.multiple ? selectValues(input) : input.type === 'checkbox' ? input.checked : input.value;
  if (input.dataset.pickerFilter === 'ability') {
    releasePickerOverride('ability');
    savePickerMemory();
    syncPickerFilterChrome(input.closest('.inline-picker'));
    updatePickerRegion(S.CERES_PICKER.deedUid);
    return;
  }
  // Land Cards and Bloodline pairs exclude each other -- switching one on switches the other off.
  const rival = ({ land_only: 'bloodline_pairs', bloodline_pairs: 'land_only' })[input.dataset.pickerFilter];
  if (rival && input.checked && S.CERES_PICKER.filters[rival]) {
    S.CERES_PICKER.filters[rival] = false;
    releasePickerOverride(rival);
    const rivalInput = input.closest('.picker-filters')?.querySelector(`[data-picker-filter="${rival}"]`);
    if (rivalInput) rivalInput.checked = false;
  }
  // Bloodline pairs shows every splinter; switching it off brings back the remembered splinter groups.
  if (S.CERES_PICKER.groupId === 'worker' && wasPairs !== Boolean(S.CERES_PICKER.filters.bloodline_pairs)) {
    seedSplinterSelection(S.CERES_PICKER.filters, S.CERES_PICKER.row);
  }
  if (input.dataset.pickerFilter !== 'q') { releasePickerOverride(input.dataset.pickerFilter); savePickerMemory(); } // the name search is per lookup
  if (input.dataset.pickerFilter === 'keep_open') { if (!input.checked) S.CERES_PICKER.chain = false; return; } // a behaviour switch, the list stays as it is
  syncPickerFilterChrome(input.closest('.inline-picker'));
  clearTimeout(S.CERES_PICKER.filterTimer);
  S.CERES_PICKER.filterTimer = setTimeout(() => fetchPickerPage(true), 180);
}

// `appendFrom` (): insert only the rows from that index on, leaving the shown rows
// (and their decoded <img>s) untouched. `footerOnly`: the list stays as it is.
export function updatePickerRegion(deedUid, { appendFrom = 0, footerOnly = false } = {}) {
  const picker = S.CERES_PICKER;
  if (!picker || picker.deedUid !== deedUid) return false;
  const pickerElement = pickerElementFor(deedUid); // one scoped lookup, not a scan of every mounted row
  if (!pickerElement) return false;
  const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);
  if (!row) return false;
  const replacing = picker.mode === 'replace' && picker.currentOccupant;
  const region = pickerRegionHtml(picker, replacing, row, { startIndex: appendFrom });
  const list = pickerElement.querySelector('.candidate-list');
  const footer = pickerElement.querySelector('.picker-footer');
  if (!list || !footer) return false;
  const affinity = pickerElement.querySelector('.candidate-worker-affinity');
  if (affinity && !appendFrom && !footerOnly) {
    affinity.querySelector('.terrain-affinity-viewport')?._terrainAffinityCleanup?.();
    affinity.innerHTML = terrainAffinityHtml(row, 'picker', picker.filters?.splinter || '');
    bindTerrainAffinityOverflow(affinity);
  }
  pickerElement.querySelectorAll('[data-picker-action="sort"]').forEach((chip) => {
    chip.classList.toggle('is-active', chip.dataset.sort === picker.sort);
  });
  // Footer chips can change a filter without touching its pill -- keep the pills truthful.
  pickerElement.querySelectorAll('input[type="checkbox"][data-picker-filter]').forEach((box) => {
    box.checked = picker.filters?.[box.dataset.pickerFilter] === true;
  });
  syncPickerFilterChrome(pickerElement);
  if (appendFrom) {
    list.insertAdjacentHTML('beforeend', region.rows);
  } else if (!footerOnly) {
    list.innerHTML = region.rows;
  }
  if (!appendFrom && !footerOnly) trimPickerGridLoadingRow(list);
  // nothing sits between the header bar and the first card -- the upgrade check
  // is a quiet footer status instead of a list row.
  const upgradeState = workerUpgradeOptionsState(row);
  const upgradeStatus = picker.groupId === 'worker' && (upgradeState.loading || picker.upgradeOptionsDemand)
    ? '<p class="picker-upgrade-loading" role="status" aria-live="polite">Checking worker upgrade options…</p>' : '';
  footer.innerHTML = upgradeStatus + region.footer;
  bindCountdowns(() => fetchPickerPage(true));
  return true;
}

export function workerSubtable(row, { mobile = false } = {}) {
  if (!EXPANDED_ROWS.has(row.deed_uid)) return '';
  const presentation = mobile ? null : presentationForRow(row);
  const dataReady = EXPANDED_DATA_LOADED.has(row.deed_uid);
  try {
    return renderSlotEditor(row, presentation, {
      dataReady, error: EXPAND_ERRORS.get(row.deed_uid),
      upgradeOptionsLoading: workerUpgradeOptionsState(row).loading,
    });
  } catch (error) {
    if (!RENDER_ERROR_LOGGED_DEEDS.has(row.deed_uid)) {
      RENDER_ERROR_LOGGED_DEEDS.add(row.deed_uid);
      const detail = error?.stack || error?.message || String(error);
      console.error(`renderSlotEditor failed for deed_uid=${row.deed_uid}: ${detail}`);
    }
    const bandRem = presentation?.geometryProfile.bandRem || 18.5125;
    let layoutCounts = { visibleSlots: 1, visibleDividers: 0, hasAbilityArc: false, hasVovArc: false };
    let layoutCountsAvailable = true;
    try { layoutCounts = editorVisibleColumnCounts(row, dataReady); } catch (_) { layoutCountsAvailable = false; }
    const layoutFallback = layoutCountsAvailable ? '' : ' data-eos-layout-counts-unavailable';
    const arcGeometry = `${layoutCounts.hasAbilityArc ? ' data-eos-ability-arc' : ''}${layoutCounts.hasVovArc ? ' data-eos-vov-arc' : ''}`;
    return `<section class="slot-editor"><div class="editor-band" data-plot-state="error" data-eos-render-error="The plot could not be displayed. Retry to reload its details."${layoutFallback}${arcGeometry} style="--eos-visible-slots:${layoutCounts.visibleSlots};--eos-visible-dividers:${layoutCounts.visibleDividers};--eos-band-height:${bandRem}rem"><div class="eos-status"><span class="eos-status-text" role="status" aria-live="polite"></span><a href="#" class="expand-retry-link" data-deed-uid="${esc(row.deed_uid)}">Retry</a><button type="button" class="eos-close chevron-close" aria-label="Close plot ${esc(formatPlotLabel(row.plot))}" data-tip="Close">${CHEVRON_CLOSE_ICON}</button></div></div></section>`;
  }
}
