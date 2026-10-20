/* theme.js — Iris collapsible theme picker ()
 Canonical home: ~/Projects/Iris/theme.js
 Vendoring: copy to <project>/assets/theme.js at deploy time.

 Usage:
 import { mountThemePicker } from './theme.js';
 mountThemePicker({ storageKey: 'myproject:theme', defaultTheme: 'dark' });
 // defaultTheme: id to use when localStorage has no saved value (omit → 'alchemical').
 // No theme's label says "(default)": the default is whatever the HOST passes here, so a
 // hardcoded label misdescribes the picker in every consumer that chooses differently.
 // the owner, 2026-09-04, after Ceres set dark and its picker still called alchemical the default.

 Adding a theme: add one row to THEMES below. Zero picker code changes needed.
 Collapsed state: one button showing the active theme emoji.
 Expanded state: full row slides leftward from the toggle, identical to the
 collapsed single-button in visual style — no new chrome.

 CSS animation: max-width transition + overflow:hidden. No JS animation frames.
 ARIA: aria-expanded on toggle, aria-pressed on each theme button.

 STYLING CONTRACT: canonical.css ships the FULL picker chrome —
 .theme-picker, .theme-tray, .theme-opt, .theme-toggle, and their states —
 as the single source of truth, cascaded byte-identical to every consumer.
 Consumers define NOTHING for the picker; a pre-commit guard in each
 consumer blocks redefinition of these selectors in any other stylesheet.
 Hosts may only reposition the picker via non-guarded hooks (e.g. a
 wrapper element's own layout rules) — never by adding local .theme-*
 rules. To change picker appearance, edit canonical.css in the hub and
 let the cascade propagate. */
"use strict";

const THEMES = [
  { id: "alchemical", label: "✨", title: "Alchemical" },
  { id: "dark",       label: "🌚", title: "Dark" },
  { id: "light",      label: "🌞", title: "Light" },
  { id: "wood",       label: "🌳", title: "Wood" },
];

function applyTheme(id) {
  document.documentElement.setAttribute("data-theme", id);
}

let _unmount = null;

export function mountThemePicker({ storageKey, defaultTheme } = {}) {
  if (_unmount) _unmount();
  if (!storageKey) { console.warn("mountThemePicker: storageKey missing, persistence disabled"); }
  // S7 / L4-02: a browser that blocks storage must not stop the board from starting: read and write the saved theme guardedly.
  let saved = null;
  try { saved = storageKey ? localStorage.getItem(storageKey) : null; } catch (_) { saved = null; }
  const fallback = THEMES.find(t => t.id === defaultTheme) ? defaultTheme : THEMES[0].id;
  const activeId = THEMES.find(t => t.id === saved) ? saved : fallback;
  applyTheme(activeId);

  /* ── Wrapper ─────────────────────────────────────────────────────────── */
  const wrap = document.createElement("div");
  wrap.className = "theme-picker";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "Theme picker");

  /* ── Tray (slides leftward when open) ───────────────────────────────── */
  const tray = document.createElement("div");
  tray.className = "theme-tray";
  tray.setAttribute("role", "group");
  tray.setAttribute("aria-label", "Choose theme");

  THEMES.forEach(({ id, label, title }) => {
    const btn = document.createElement("button");
    btn.className = "theme-opt" + (id === activeId ? " theme-opt--active" : "");
    btn.dataset.theme = id;
    btn.textContent = label;
    btn.title = title;
    btn.setAttribute("aria-pressed", id === activeId ? "true" : "false");
    btn.addEventListener("click", () => selectTheme(id));
    tray.appendChild(btn);
  });

  /* ── Toggle (always visible — shows active emoji) ───────────────────── */
  const toggle = document.createElement("button");
  toggle.className = "theme-opt theme-toggle";
  toggle.setAttribute("aria-expanded", "false");
  const activeTheme = THEMES.find(t => t.id === activeId);
  toggle.textContent = activeTheme ? activeTheme.label : "⚗";
  toggle.setAttribute("aria-label", `Theme: ${activeTheme ? activeTheme.title : activeId}`);

  /* DOM order [tray, toggle] with CSS `flex-direction: row`: the toggle is the
 last (rightmost) child, so it stays glued to the container's right edge while
 the tray expands leftward (container is position:fixed; right:0). */
  wrap.appendChild(tray);
  wrap.appendChild(toggle);

  /* ── State helpers ──────────────────────────────────────────────────── */
  function openPicker() {
    wrap.classList.add("is-open");
    toggle.setAttribute("aria-expanded", "true");
  }

  function closePicker() {
    wrap.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
  }

  function selectTheme(id) {
    applyTheme(id);
    if (storageKey) { try { localStorage.setItem(storageKey, id); } catch (_) { /* theme still applies for this visit */ } }
    const t = THEMES.find(th => th.id === id);
    if (t) {
      toggle.textContent = t.label;
      toggle.setAttribute("aria-label", `Theme: ${t.title}`);
    }
    tray.querySelectorAll(".theme-opt").forEach(b => {
      const isActive = b.dataset.theme === id;
      b.setAttribute("aria-pressed", isActive ? "true" : "false");
      b.classList.toggle("theme-opt--active", isActive);
    });
    closePicker();
  }

  /* ── Event wiring ───────────────────────────────────────────────────── */
  toggle.addEventListener("click", () => {
    wrap.classList.contains("is-open") ? closePicker() : openPicker();
  });

  function onOutsideClick(e) {
    if (!wrap.contains(e.target)) closePicker();
  }

  function onEscape(e) {
    if (e.key === "Escape" && wrap.classList.contains("is-open")) {
      closePicker();
      toggle.focus();
    }
  }

  document.addEventListener("click", onOutsideClick);
  document.addEventListener("keydown", onEscape);

  const host = document.querySelector("#header .container, #header, .header") || document.body;
  host.appendChild(wrap);

  _unmount = () => {
    document.removeEventListener("click", onOutsideClick);
    document.removeEventListener("keydown", onEscape);
    wrap.remove();
    _unmount = null;
  };
}
