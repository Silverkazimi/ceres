/* *
 * customSelect.js — lightweight custom dropdown component
 *
 * Originally written for a sibling project of the same author (232 lines at the time of the copy), then modified here. Ceres stays
 * self-contained (no cross-project import): this is a standalone copy.
 * Modified 2026-08-25 (dropdown-unification batch): the per-instance outside-
 * click `document.addEventListener` was replaced with one delegated listener
 * shared by every instance -- see `_wireDocumentOutsideClick()`'s own comment
 * for why (Ceres now re-initialises this component on repeatedly-recreated
 * DOM, which the original per-instance listener would leak on).
 *
 * Wraps a hidden <select> with a fully CSS-styled trigger + dropdown list.
 * The hidden <select> stays live: .value and change events work unchanged,
 * so Ceres's existing filter logic (matchesFilters()/populateFilterOptions()
 * in app.js) needed zero changes to keep working.
 *
 * Public API (unchanged from the Asdroid source):
 * initCustomSelect(selectEl, options) — call once per <select> to set up
 * syncCustomSelect(selectEl) — call after programmatic option/value changes
 *
 * Ceres-local addition (not present in the Asdroid source, added for this
 * project's own need -- the owner, 2026-08-21 board-polish batch 9: "filter
 * dropdown options should carry the board's visual language, e.g. terrain
 * options get their terrain background colour and emblem icon before the
 * text"): an optional `options.decorateOption(value, text)` callback. When
 * provided it is called for every rendered <li> (and, unless `decorateTrigger`
 * is also given, for the closed trigger's own label) and may return
 * `{ icon, className }` -- `icon` is raw HTML prepended before the escaped
 * option text, `className` is an extra CSS class added to that option's <li>
 * (used for the terrain background-fill case). Returning a falsy value
 * renders the option as plain text, same as the unmodified Asdroid behaviour.
 * This is the same kind of per-project extension point the Asdroid original
 * already uses, and the public initCustomSelect/syncCustomSelect signatures
 * and existing option-rendering behaviour are unchanged for callers that
 * don't pass decorateOption.
 *
 * Look 2 (maintainer-accepted plan, Variant A -- "beautiful and compact,
 * use icons but don't show them all at once"): two further optional,
 * opt-in-only hooks, added for the board's compact filter triggers. Neither
 * changes a caller that omits it, and neither touches the shared
 * `.picker-filters`/worker-picker defaults.
 * - `options.ariaLabel` (string) -- set once as the trigger button's own
 * `aria-label` at init time, so a compact trigger whose visible text is
 * just the chosen value (or the filter name when empty) still announces which
 * filter it is to a screen reader, the way the removed visible caption
 * used to.
 * - `options.decorateTrigger(value, text)` -- like `decorateOption`, but
 * for the CLOSED TRIGGER LABEL ONLY; when given it takes over the
 * trigger's rendering completely (the matching <li> in the open list
 * still renders from `decorateOption`, unchanged). Returns
 * `{ icon, label, className }`: `icon` is raw HTML prepended before the
 * text, `label` overrides the visible text (defaults to the option's own
 * `text` when omitted -- this is how Region's closed trigger can show
 * only its number while the option list keeps number + name), and
 * `className` is added to the trigger element itself (this is how a
 * compact Terrain trigger can carry a small territory-style colour
 * swatch without the trigger being painted as a full list row).
 *
 * : opt-in
 * multi-select. A select becomes multi-select when it carries the native
 * `multiple` attribute or is initialised with `options.multiple: true` (which
 * sets `selectEl.multiple`), so every sort select and every caller that passes
 * neither keeps the single-select behaviour unchanged. In multi mode:
 * - the list is `aria-multiselectable="true"`; clicking an option, or Space/
 * Enter on the focused one, TOGGLES it and keeps the list open; Esc closes;
 * - the value-less clearing option clears the whole choice (and closes);
 * nothing selected and the value-less clearing option selected mean the same thing;
 * it is never selected together with a real value, so `selectEl.value` stays
 * truthy exactly while the filter is active (callers testing `.value`
 * keep working);
 * - each option carries a check mark, shown while it is selected;
 * - the closed trigger reads `options.emptyLabel` when configured, otherwise
 * the value-less option's label; it reads the option itself (one,
 * with its normal decoration), "Plains, River" (two that fit
 * `options.summaryMaxChars`, default 18) or "3 terrains" (the plural noun
 * comes from `options.pluralNoun`).
 * `selectValues(selectEl)` / `setSelectValues(selectEl, values)` read and
 * write the chosen values for single and multi selects alike. */

"use strict";

function _escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* *
 * Initialise a custom dropdown for the given native <select> element.
 * Idempotent — calling twice on the same element is a no-op.
 *
 * options.decorateOption — optional (value, text) => { icon, className } | null.
 * See file header above; Ceres-local addition. */
export function initCustomSelect(selectEl, options = {}) {
  if (!selectEl || selectEl._csel) return;
  if (options.multiple) selectEl.multiple = true; // opt-in only
  const multi = Boolean(selectEl.multiple);

  // Hide the native select but keep it in the DOM so .value / events work
  selectEl.style.cssText = "position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;";

  // Wrapper
  const wrap = document.createElement("div");
  wrap.className = "csel-wrap";
  selectEl.parentNode.insertBefore(wrap, selectEl);
  wrap.appendChild(selectEl);

  // Trigger button
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "csel-trigger ceres-btn ceres-btn--secondary";
  trigger.dataset.controlDensity = "compact";
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  // Look 2: static for the trigger's whole lifetime (the category never
  // changes), so it is set once here and never touched by _rebuildOptions().
  if (options.ariaLabel) trigger.setAttribute("aria-label", options.ariaLabel);
  wrap.appendChild(trigger);

  // Option list
  const list = document.createElement("ul");
  list.className = "csel-list";
  if (typeof list.showPopover === "function") list.setAttribute("popover", "manual");
  list.setAttribute("role", "listbox");
  if (multi) list.setAttribute("aria-multiselectable", "true");
  list.hidden = true;
  wrap.appendChild(list);

  // Store ref on the native element (options kept for _rebuildOptions)
  selectEl._csel = { trigger, list, wrap, options };

  // Populate from current options
  _rebuildOptions(selectEl);

  // Event wiring

  // Toggle open/close on trigger click
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !list.hidden;
    if (open) {
      _close(selectEl);
    } else {
      _closeAll();
      _open(selectEl);
    }
  });

  // Select option on li click
  list.addEventListener("click", (e) => {
    const li = e.target.closest(".csel-option");
    if (li && !li.classList.contains("is-disabled")) _selectOption(selectEl, li.dataset.value);  // a disabled <option> stays unpickable
  });
  // a multi list stays open across clicks, so keep keyboard focus on
  // the trigger (Space/Enter/arrows keep working after a mouse toggle).
  if (multi) list.addEventListener("mousedown", (e) => e.preventDefault());

  // Keyboard navigation on trigger
  trigger.addEventListener("keydown", (e) => {
    const opts = [...list.querySelectorAll(".csel-option:not(.is-disabled)")];
    const focused = list.querySelector(".is-focused");
    const idx = focused ? opts.indexOf(focused) : -1;

    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) { _open(selectEl); return; }
      const next = e.key === "ArrowDown"
        ? Math.min(idx + 1, opts.length - 1)
        : Math.max(idx - 1, 0);
      opts.forEach((o, i) => o.classList.toggle("is-focused", i === next));
      opts[next]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (list.hidden) { _open(selectEl); return; }
      if (focused) _selectOption(selectEl, focused.dataset.value);
    } else if (e.key === "Escape") {
      _close(selectEl);
    }
  });

  // Close on outside click. Wired ONCE for the whole document (see
  // `_wireDocumentOutsideClick` below), never one `document.addEventListener`
  // per instance -- Ceres dropdown-unification batch (2026-08-25): the
  // picker's filter selects are torn down and recreated as fresh DOM nodes on
  // every re-render (updateMountedRow()/renderRow() in app.js rebuild that
  // whole <td> via innerHTML), unlike the main table's selects, which are
  // created once. A per-instance `document.addEventListener` here would never
  // be removed when its `selectEl`/`wrap` are discarded -- innerHTML
  // replacement orphans the DOM nodes but does nothing to a listener attached
  // to `document` that still closes over them -- an unbounded listener leak
  // one more per re-render, forever. The single delegated listener below
  // looks up whichever `.csel-wrap` elements are actually in the document at
  // click time, so an orphaned instance is simply garbage-collected with no
  // listener keeping it alive.
  _wireDocumentOutsideClick();

  // Sync the trigger label on any native "change" — covers programmatic
  // value + dispatchEvent (Playwright selectOption, deep-links, applyState
  // style restores) that bypass the component's own click path, which never
  // otherwise updates .csel-trigger. The component's own _selectOption()
  // already dispatches "change" itself, so this handler re-runs on the
  // click path too — idempotent (rebuildOptions is pure re-render from
  // current .value) and loop-free (it dispatches nothing).
  selectEl.addEventListener("change", () => syncCustomSelect(selectEl));
}

/* *
 * Sync the custom dropdown display after options or value are changed
 * programmatically (e.g. after rebuilding options or restoring state). */
export function syncCustomSelect(selectEl) {
  if (!selectEl || !selectEl._csel) return;
  _rebuildOptions(selectEl);
}

/* *
 * : the chosen non-empty values of a select, single or multi, in option
 * order (the value-less clearing option / no choice -> []). Tolerates a select-like stand-in without
 * `selectedOptions` (falls back to `.value`). */
export function selectValues(selectEl) {
  if (!selectEl) return [];
  const chosen = selectEl.selectedOptions;
  if (!chosen || typeof chosen.length !== "number") return selectEl.value ? [String(selectEl.value)] : [];
  return [...chosen].map((opt) => opt.value).filter(Boolean);
}

/* *
 * : choose exactly `values` (unknown values are dropped). A single
 * select takes the first known value. Nothing known -> the value-less clearing option.
 * Fires no event (same contract as a programmatic `.value =`). */
export function setSelectValues(selectEl, values) {
  if (!selectEl) return;
  const wanted = new Set((Array.isArray(values) ? values : [values]).filter((v) => v != null && v !== "").map(String));
  const opts = [...(selectEl.options || [])];
  if (!selectEl.multiple) {
    const hit = opts.find((opt) => opt.value && wanted.has(opt.value));
    selectEl.value = hit ? hit.value : "";
    return;
  }
  let any = false;
  for (const opt of opts) {
    opt.selected = Boolean(opt.value) && wanted.has(opt.value);
    any = any || opt.selected;
  }
  if (!any) selectEl.value = "";
}

/* *
 * : the closed-trigger text for a multi choice of `labels` (already the
 * visible per-option labels). Pure, exported for the tests. */
export function multiSummaryLabel(labels, anyText, { pluralNoun, summaryMaxChars = 18 } = {}) {
  if (!labels.length) return anyText || "";
  if (labels.length === 1) return labels[0];
  const joined = labels.join(", ");
  if (labels.length === 2 && joined.length <= summaryMaxChars) return joined;
  return `${labels.length} ${pluralNoun || _pluralNoun(anyText)}`;
}

function _pluralNoun(anyText) {
  const raw = String(anyText || "").replace(/^(?:all|any)\s+/i, "").replace(/\s+filter$/i, "").trim().toLowerCase();
  const fixed = { region: "regions", rarity: "rarities", terrain: "terrains", worksite: "worksites", type: "types", badges: "badges", badge: "badges", set: "sets", foil: "foils", splinter: "splinters", status: "statuses", level: "levels" };
  if (fixed[raw]) return fixed[raw];
  const noun = raw || "values";
  if (/[^aeiou]y$/.test(noun)) return `${noun.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/.test(noun)) return `${noun}es`;
  return `${noun}s`;
}

// Private helpers

function _valueHtml(icon, label, arrow = false) {
  return `<span class="csel-value">${icon || ""}<span class="csel-value-text">${_escapeHtml(label)}</span></span>${arrow ? '<span class="csel-arrow ceres-chevron" aria-hidden="true"></span>' : ''}`;
}

function _rebuildOptions(selectEl) {
  const { trigger, list, options } = selectEl._csel;
  // A filter's empty option clears it. Keep legacy source option wording out
  // of the trigger and show "All" only in the open list (K-4).
  const filterName = options.ariaLabel?.match(/^(.+?) filter$/i)?.[1] || "";
  const emptyName = options.emptyLabel || selectEl.dataset.emptyLabel || filterName;
  const multi = Boolean(selectEl.multiple);
  const anyChosen = multi && selectValues(selectEl).length > 0;
  // a multi list is rebuilt while open (after each toggle); keep the
  // keyboard focus on the same option across the re-render.
  const focusedValue = list.querySelector(".is-focused")?.dataset.value;
  list.innerHTML = "";

  // Walk the live children tree so <optgroup> labels render as non-selectable
  // group headers (kept functional: option.value drives selection, not clicks
  // on headers). Flat selects without optgroups render exactly as before.
  const renderOption = (opt) => {
    const li = document.createElement("li");
    li.className = "csel-option";
    li.setAttribute("role", "option");
    li.dataset.value = opt.value;
    const optionText = opt.value || !emptyName ? opt.textContent : "All";
    const deco = options.decorateOption ? options.decorateOption(opt.value, optionText) : null;
    if (deco) {
      if (deco.className) li.className += ` ${deco.className}`;
      li.innerHTML = _valueHtml(deco.icon, optionText);
    } else {
      li.innerHTML = _valueHtml("", optionText);
    }
    if (multi) li.insertAdjacentHTML("beforeend", '<span class="csel-check" aria-hidden="true"></span>');
    if (opt.disabled) { li.classList.add("is-disabled"); li.setAttribute("aria-disabled", "true"); }
    if (multi ? (opt.value ? opt.selected : !anyChosen) : opt.value === selectEl.value) {
      li.classList.add("is-selected");
      li.setAttribute("aria-selected", "true");
    } else {
      li.setAttribute("aria-selected", "false");
    }
    list.appendChild(li);
  };

  for (const child of selectEl.children) {
    if (child.tagName === "OPTGROUP") {
      if (child.label) {
        const head = document.createElement("li");
        head.className = "csel-group-header";
        head.setAttribute("role", "presentation");
        head.textContent = child.label;
        list.appendChild(head);
      }
      for (const opt of child.children) {
        if (opt.tagName === "OPTION") renderOption(opt);
      }
    } else if (child.tagName === "OPTION") {
      renderOption(child);
    }
  }

  // Legacy flat path (kept for selects whose options are mutated at runtime
  // via HTMLOptionsCollection API rather than child nodes).
  if (!list.querySelector(".csel-option")) {
    [...selectEl.options].forEach(renderOption);
  }
  if (multi && focusedValue != null && !list.hidden) {
    [...list.querySelectorAll(".csel-option")].find((li) => li.dataset.value === focusedValue)?.classList.add("is-focused");
  }

  // Update trigger label. Look 2: `decorateTrigger`, when given, takes
  // over the trigger's rendering completely (own icon/label/className)
  // this is how Region's closed trigger can show just its number while its
  // list row keeps number + name, and how a compact trigger can carry a
  // colour swatch the matching <li> does not need. Callers that only pass
  // `decorateOption` (every pre-Look-2 caller) get the original behaviour
  // unchanged: the trigger reads like its matching <li>, not just the open
  // list.
  // The class this function itself last added is removed (and only that
  // never a class some OTHER caller manages directly on the same trigger
  // e.g. board.js's colourRegionControl() territory-* classes) before adding
  // whatever decorateTrigger returns this time, tracked in a data attribute
  // rather than a blanket `className` reset so the two ownership domains
  // never collide.
  if (trigger.dataset.decoClass) {
    trigger.classList.remove(...trigger.dataset.decoClass.split(" ").filter(Boolean));
    delete trigger.dataset.decoClass;
  }
  trigger.dataset.filterActive = String(Boolean(selectEl.value));
  if (multi && selectValues(selectEl).length > 1) {
    const chosen = [...selectEl.selectedOptions].filter((opt) => opt.value);
    const labels = chosen.map((opt) => options.decorateTrigger?.(opt.value, opt.textContent)?.label ?? opt.textContent);
    const anyOpt = [...selectEl.options].find((opt) => !opt.value);
    const summaryOptions = { ...options, pluralNoun: options.pluralNoun || _pluralNoun(options.ariaLabel?.replace(/\s+filter$/i, "")) };
    const icons = chosen.map((opt) => options.decorateTrigger?.(opt.value, opt.textContent)?.icon || options.decorateOption?.(opt.value, opt.textContent)?.icon || "");
    const allHaveIcons = icons.every(Boolean);
    // two or three icon choices stay visual; four or more fold to a count.
    if (allHaveIcons && chosen.length <= 3) {
      trigger.innerHTML = _valueHtml(icons.map((icon) => `<span class="csel-picked-icon" aria-hidden="true">${icon}</span>`).join(""), "", true);
      // The shared value gap serves icon + text; selected icons need the standard tight 2px gap.
      trigger.querySelector('.csel-value').style.gap = '2px';
    } else {
      trigger.innerHTML = _valueHtml("", allHaveIcons ? `${chosen.length} ${summaryOptions.pluralNoun}` : multiSummaryLabel(labels, anyOpt?.textContent, summaryOptions), true);
    }
    trigger.setAttribute("aria-label", `${options.ariaLabel || "Filter"}: ${labels.join(", ")}`);
    trigger.title = labels.join(", ");
    return;
  }
  const sel = multi
    ? ([...selectEl.options].find((opt) => opt.selected && opt.value) || [...selectEl.options].find((opt) => !opt.value) || selectEl.options[selectEl.selectedIndex])
    : selectEl.options[selectEl.selectedIndex];
  const label = sel ? sel.textContent : "";
  const category = options.ariaLabel?.replace(/\s+filter$/i, "") || selectEl.getAttribute("aria-label")
    || selectEl._csel.wrap.parentElement?.querySelector(".sr-only")?.textContent?.trim()
    || selectEl.id?.replace(/(?:-filter)?$/, "").replace(/[-_]/g, " ") || "Filter";
  const triggerLabel = sel && !sel.value ? (emptyName || (/^all$/i.test(label) ? category : label)) : label;
  if (multi) {
    trigger.setAttribute("aria-label", sel?.value ? `${options.ariaLabel || category}: ${label}` : (options.ariaLabel || emptyName || category));
    trigger.title = sel?.value ? label : "";
  }
  if (sel && options.decorateTrigger) {
    const tDeco = options.decorateTrigger(sel.value, label);
    if (tDeco) {
      if (tDeco.className) {
        trigger.classList.add(...tDeco.className.split(" ").filter(Boolean));
        trigger.dataset.decoClass = tDeco.className;
      }
      trigger.innerHTML = _valueHtml(tDeco.icon, tDeco.label ?? triggerLabel, true);
      return;
    }
  }
  const deco = sel && options.decorateOption ? options.decorateOption(sel.value, label) : null;
  if (deco) {
    trigger.innerHTML = _valueHtml(deco.icon, triggerLabel, true);
  } else {
    trigger.innerHTML = _valueHtml("", triggerLabel, true);
  }
}

function _selectOption(selectEl, value) {
  if (selectEl.multiple) { _toggleOption(selectEl, value); return; }
  selectEl.value = value;
  selectEl.dispatchEvent(new Event("change", { bubbles: true }));
  _rebuildOptions(selectEl);
  _close(selectEl);
}

// multi mode -- a real value toggles and the list stays open; the
// The value-less clearing option clears the choice and closes.
function _toggleOption(selectEl, value) {
  const { trigger, list } = selectEl._csel;
  if (!value) {
    setSelectValues(selectEl, []);
  } else {
    if (![...selectEl.options].some((o) => o.value === value)) return;
    const next = new Set(selectValues(selectEl));
    if (next.has(value)) next.delete(value); else next.add(value);
    setSelectValues(selectEl, [...next]);
  }
  selectEl.dispatchEvent(new Event("change", { bubbles: true }));
  _rebuildOptions(selectEl);
  if (!value) { _close(selectEl); return; }
  if (!list.hidden) _positionList(trigger, list);
}

function _positionList(trigger, list) {
  const anchor = trigger.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  const height = window.visualViewport?.height || window.innerHeight;
  const above = Math.max(0, anchor.top - 8);
  const below = Math.max(0, height - anchor.bottom - 8);
  const upward = below < Math.min(list.scrollHeight, above);
  Object.assign(list.style, {
    position: 'fixed', inset: 'auto', margin: '0',
    minWidth: `${Math.min(anchor.width, width - 16)}px`,
    maxWidth: `${width - 16}px`, maxHeight: `${upward ? above : below}px`,
  });
  const popup = list.getBoundingClientRect();
  list.style.left = `${Math.max(8, Math.min(anchor.left, width - popup.width - 8))}px`;
  list.style.top = `${upward ? Math.max(8, anchor.top - popup.height - 2) : anchor.bottom + 2}px`;
}

function _open(selectEl) {
  const { trigger, list } = selectEl._csel;
  list.hidden = false;
  if (list.hasAttribute("popover")) list.showPopover();
  _positionList(trigger, list);
  trigger.setAttribute("aria-expanded", "true");
  // Focus the currently selected item
  const selected = list.querySelector(".is-selected");
  if (selected) selected.classList.add("is-focused");
}

function _close(selectEl) {
  if (!selectEl._csel) return;
  const { trigger, list } = selectEl._csel;
  if (list.hasAttribute("popover") && list.matches(":popover-open")) list.hidePopover();
  list.hidden = true;
  trigger.setAttribute("aria-expanded", "false");
  list.querySelectorAll(".is-focused").forEach(el => el.classList.remove("is-focused"));
}

function _closeAll() {
  document.querySelectorAll(".csel-list:not([hidden])").forEach(list => {
    const wrap = list.closest(".csel-wrap");
    const sel = wrap?.querySelector("select");
    if (sel) _close(sel);
  });
}

// One CAPTURE-phase document listener for the whole page (idempotent -- a
// module-level flag, not per-selectEl), closing any open dropdown whose
// `.csel-wrap` does not contain the click target. CAPTURE phase, not bubble
// so this still sees a click even when it lands on something that calls
// `stopPropagation()` during the bubble phase (Ceres's slot editor does this
// for every `[data-slot-action]` tile). See the call site in
// initCustomSelect() for why this replaced a per-instance listener.
let _DOCUMENT_OUTSIDE_CLICK_WIRED = false;
function _wireDocumentOutsideClick() {
  if (_DOCUMENT_OUTSIDE_CLICK_WIRED) return;
  _DOCUMENT_OUTSIDE_CLICK_WIRED = true;
  window.addEventListener("resize", _closeAll);
  document.addEventListener("scroll", (event) => {
    // A text field scrolling its own text (a long search term scrolls back to its start when
    // the field loses focus -- e.g. on the click that opens a drop-down) moves no anchor.
    if (event.target instanceof Element && event.target.matches("input, textarea")) return;
    if (!(event.target instanceof Element) || !event.target.closest(".csel-list")) _closeAll();
  }, true);
  document.addEventListener("click", (e) => {
    document.querySelectorAll(".csel-wrap").forEach((wrap) => {
      if (wrap.contains(e.target)) return;
      const sel = wrap.querySelector("select");
      if (sel) _close(sel);
    });
  }, true);
}
