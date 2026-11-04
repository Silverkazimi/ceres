// design 4 (maintainer go ): the desktop header's Menu. One list in three columns (Places, Tools, Player); each
// entry runs exactly what today's control runs -- the page passes those handlers in (app.js on the board, card-browser.js on
// Cards), this module adds no behaviour of its own. Desktop only: ceres.css shows #hdr-menu-btn and #hdr-place only outside
// the phone tier (html:not(.ceres-lw-le-1279), P-0), so the phone header and its own controls stay exactly as they were.
// Plain ES module with no imports; every top-level name starts with `hdrMenu` because the test VM evaluates the board's
// whole module graph in one scope.

export const HDR_MENU_GROUPS = [
  { title: 'Places', entries: [
    { key: 'plots', name: 'Plots', desc: 'Your land, plot by plot', icon: 'plots' },
    { key: 'myCards', name: 'My cards', desc: 'Your card collection', icon: 'myCards' },
    { key: 'market', name: 'Market', desc: 'Find and buy cards', icon: 'market' },
    { key: 'purchases', name: 'My purchases', desc: 'Cards you bought', icon: 'purchases' },
  ] },
  { title: 'Tools', entries: [
    { key: 'harvest', name: 'Harvest', desc: 'Review and collect resources', icon: 'harvest' },
    { key: 'changes', name: 'Changes', desc: 'Staged changes to sign', icon: 'changes' },
    { key: 'planning', name: 'Planning', desc: 'Plan placements on this board', icon: 'plots' },
    { key: 'combine', name: 'Find combinables', desc: 'Cards you can combine', icon: 'cards' },
  ] },
  { title: 'Player', entries: [
    { key: 'refresh', name: 'Refresh', desc: 'Reload this player’s data', icon: 'refresh' },
    { key: 'account', name: 'Account', desc: 'Sign in and your keys', icon: 'account' },
  ] },
];

// The page's name beside Menu and the Places entry marked current. Find combinables is a view of My cards ().
export function hdrMenuPlace(page, cbMode) {
  if (page !== 'cards' && document.body?.dataset?.boardMode === 'planning') return { key: 'plots', name: `Planning · ${document.body.dataset.boardPlanName || 'Plan 1'}` };
  if (page !== 'cards') return { key: 'plots', name: 'Plots' };
  if (cbMode === 'market') return { key: 'market', name: 'Market' };
  if (cbMode === 'purchases') return { key: 'purchases', name: 'My purchases' };
  return { key: 'myCards', name: 'My cards' };
}

const HDR_MENU_LINE_ICON = (d) => `<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const HDR_MENU_REFRESH_ICON = HDR_MENU_LINE_ICON('<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>');
const HDR_MENU_CLOSE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

// Icons are the app's own where they exist: the switcher set (header-icons.js), the live Harvest and Changes drawings, and
// the account button's wallet logo (the keychain image when the page has no account button).
function hdrMenuIcon(name) {
  const set = (typeof window !== 'undefined' && window.ceresHeaderIcons?.ICONS) || {};
  if (set[name]) return set[name];
  if (name === 'refresh') return HDR_MENU_REFRESH_ICON;
  if (name === 'harvest' || name === 'changes') {
    const svg = document.querySelector(name === 'harvest' ? '#harvest-btn svg' : '#checkout-btn svg');
    return svg ? svg.outerHTML : '';
  }
  if (name === 'account') {
    const logo = document.querySelector('#account-menu-btn img');
    const src = logo?.getAttribute('src') || '/static/vendor/wallet/keychain.svg';
    return `<img class="hdr-ic hdr-menu-img" src="${String(src).replace(/"/g, '&quot;')}" alt="" aria-hidden="true">`;
  }
  return '';
}

// IN-0267: the Planning entry reads "Live" while the board is in planning, so the same entry returns.
function hdrMenuEntryLabel(item) {
  if (item.key === 'planning' && document.body?.dataset?.boardMode === 'planning') return { ...item, name: 'Live', desc: 'Back to the live board' };
  return item;
}

function hdrMenuEscape(value) { return String(value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]); }

export function hdrMenuVariant() { return document.body?.dataset?.hdrMenuVariant === 'B' ? 'B' : 'A'; }

function hdrMenuSetupItems(setups) {
  if (!setups) return [];
  if (setups.readOnly) return setups.readOnlyReason?.includes('previous good revision')
    ? [{ key: 'setup-restore', name: 'Restore last saved setups', desc: 'Restore the previous good revision', icon: 'plots' }] : [];
  const list = setups.plans.map((plan) => ({ key: `setup:${encodeURIComponent(plan.id)}`,
    name: plan.name, selected: plan.id === setups.selectedId,
    desc: plan.id === setups.selectedId ? 'Current setup' : 'Switch setup', icon: 'plots' }));
  return [...list,
    ...(setups.dirty ? [{ key: 'setup-retry', name: 'Save again', desc: 'Retry saving unsaved setup changes', icon: 'plots' }] : []),
    { key: 'setup-new', name: 'New setup', desc: 'Start with an empty sheet', icon: 'plots' },
    { key: 'setup-live', name: 'Start from live', desc: 'Copy the live board', icon: 'plots' },
    { key: 'setup-duplicate', name: 'Duplicate setup', desc: 'Copy current setup', icon: 'plots' },
    ...(hdrMenuVariant() === 'B' && document.body?.dataset?.boardMode === 'planning' ? [{ key: 'setup-back', name: 'Back to live', desc: 'Return to the live board', icon: 'plots' }] : [])];
}

export function hdrMenuPanelHtml(currentKey, setups = null) {
  const variant = hdrMenuVariant();
  const entry = (raw) => { const item = hdrMenuEntryLabel(raw); const planning = item.key === 'planning' && variant === 'B';
    const name = planning ? (document.body?.dataset?.boardMode === 'planning' ? `Planning · ${document.body.dataset.boardPlanName || 'Plan 1'} ›` : 'Planning ›') : item.name;
    return `<li><button type="button" class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass" data-control-density="comfortable" data-menu-entry="${hdrMenuEscape(item.key)}"${planning ? ' aria-haspopup="true" aria-expanded="false"' : ''}${item.key === currentKey ? ' aria-current="page"' : ''}>${hdrMenuIcon(item.icon)}<span class="hdr-menu-txt"><span class="hdr-menu-name">${hdrMenuEscape(name)}</span><span class="hdr-menu-desc">${hdrMenuEscape(planning ? 'Choose a setup' : item.desc)}</span></span></button></li>`; };
  const setupEntry = (item) => item.key.startsWith('setup:') ? `<li class="hdr-menu-setup-row"><button type="button" class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass" data-control-density="comfortable" data-menu-entry="${hdrMenuEscape(item.key)}"><span class="hdr-menu-txt"><span class="hdr-menu-name">${item.selected ? '✓ ' : ''}${hdrMenuEscape(item.name)}</span></span></button><button type="button" class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass" data-control-density="compact" data-setup-rename="${hdrMenuEscape(item.key.slice(6))}" aria-label="Rename ${hdrMenuEscape(item.name)}">Rename</button><button type="button" class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass" data-control-density="compact" data-setup-delete="${hdrMenuEscape(item.key.slice(6))}" aria-label="Delete ${hdrMenuEscape(item.name)}">Delete</button></li>` : entry(item);
  const setupList = () => hdrMenuSetupItems(setups).map(setupEntry).join('');
  const column = (group, index) => `<div class="hdr-menu-col"><h3 id="hdr-menu-group-${index}">${group.title}</h3><ul aria-labelledby="hdr-menu-group-${index}">${group.entries.map(entry).join('')}</ul></div>`;
  const setupsColumn = variant === 'A' && setups && document.body?.dataset?.boardMode === 'planning' ? `<div class="hdr-menu-col hdr-menu-setups"><h3>Setups</h3><ul>${setupList()}</ul></div>` : '';
  const submenu = variant === 'B' ? `<div class="hdr-menu-submenu" data-menu-submenu hidden><h3>Setups</h3><ul>${setupList()}</ul></div>` : '';
  return `<div class="hdr-menu-top"><h2 id="hdr-menu-title">Menu</h2><button type="button" class="harvest-x hdr-menu-close ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-mode="icon" data-menu-close aria-label="Close menu">${HDR_MENU_CLOSE_ICON}</button></div>`
    + `<div class="hdr-menu-cols">${HDR_MENU_GROUPS.map(column).join('')}${setupsColumn}</div>${submenu}`;
}

// options: { page: 'board' | 'cards', actions: { [entry key]: () => void } }
export function mountHeaderMenu(options = {}) {
  const button = document.getElementById('hdr-menu-btn');
  const place = document.getElementById('hdr-place');
  if (!button) return null;
  const page = options.page === 'cards' ? 'cards' : 'board';
  const actions = options.actions || {};
  const panel = document.createElement('section');
  panel.id = 'hdr-menu';
  panel.className = 'hdr-menu-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-labelledby', 'hdr-menu-title');
  panel.hidden = true;
  document.body.append(panel);
  let open = false;

  const current = () => hdrMenuPlace(page, document.body?.dataset?.cbMode);
  const syncPlace = () => {
    const now = current();
    if (place && place.textContent !== now.name) place.textContent = now.name;
    panel.querySelectorAll('[data-menu-entry]').forEach((el) => {
      if (el.dataset.menuEntry === now.key) el.setAttribute('aria-current', 'page');
      else el.removeAttribute('aria-current');
    });
  };
  window.addEventListener('ceres:board-planning-mode', syncPlace);
  const position = () => {
    if (!open) return;
    const box = button.getBoundingClientRect();
    const header = button.closest('header')?.getBoundingClientRect();
    // unrounded: the panel's edge lines up with the Menu button's even when the header puts it on a fraction of a pixel
    panel.style.insetInlineStart = `${box.left}px`;
    panel.style.insetBlockStart = `${(header ? header.bottom : box.bottom) + 8}px`;
  };
  const entries = () => [...panel.querySelectorAll('[data-menu-entry]')];
  const submenu = () => panel.querySelector('[data-menu-submenu]');
  const setSubmenu = (next, { focus = false } = {}) => {
    const sub = submenu();
    if (!sub) return;
    sub.hidden = !next;
    const trigger = panel.querySelector('[data-menu-entry="planning"]');
    trigger?.setAttribute('aria-expanded', String(next));
    if (focus) (next ? sub.querySelector('[data-menu-entry], [data-setup-rename]') : trigger)?.focus();
  };
  const setOpen = (next, { focusButton = false } = {}) => {
    if (next === open) return;
    open = next;
    if (open) {
      panel.innerHTML = hdrMenuPanelHtml(current().key, options.setups?.());  // fresh icons and setup list
      panel.hidden = false;
      position();
      (panel.querySelector('[data-menu-entry][aria-current="page"]') || entries()[0])?.focus({ preventScroll: true });
    } else {
      setSubmenu(false);
      panel.hidden = true;
      if (focusButton) button.focus({ preventScroll: true });
    }
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-pressed', String(open));
  };

  button.addEventListener('click', () => setOpen(!open));
  panel.addEventListener('mouseover', (event) => {
    if (hdrMenuVariant() === 'B' && event.target.closest?.('[data-menu-entry="planning"]')) setSubmenu(true);
  });
  panel.addEventListener('click', (event) => {
    if (event.target.closest?.('[data-menu-close]')) { setOpen(false, { focusButton: true }); return; }
    const rename = event.target.closest?.('[data-setup-rename]');
    if (rename) {
      const row = rename.closest('.hdr-menu-setup-row');
      const selected = row?.querySelector('[data-menu-entry]');
      const name = selected?.querySelector('.hdr-menu-name');
      if (!name) return;
      const original = name.textContent.replace(/^✓ /, '');
      const input = document.createElement('input');
      input.className = 'hdr-menu-rename';
      input.setAttribute('aria-label', 'Setup name');
      input.value = original;
      selected.hidden = true;
      selected.after(input);
      rename.hidden = true;
      const finish = (save) => {
        if (!input.isConnected) return;
        const value = input.value.trim();
        input.remove();
        selected.hidden = false;
        rename.hidden = false;
        if (save && value && value !== original) {
          setOpen(false, { focusButton: true });
          options.setupAction?.('rename', value, decodeURIComponent(rename.dataset.setupRename));
        } else rename.focus();
      };
      input.addEventListener('keydown', (keyEvent) => {
        if (keyEvent.key === 'Enter' || keyEvent.key === 'Escape') { keyEvent.preventDefault(); keyEvent.stopPropagation(); finish(keyEvent.key === 'Enter'); }
      });
      input.focus(); input.select();
      return;
    }
    const del = event.target.closest?.('[data-setup-delete]');
    if (del) { setOpen(false, { focusButton: true }); options.setupAction?.('delete', decodeURIComponent(del.dataset.setupDelete)); return; }
    const entry = event.target.closest?.('[data-menu-entry]');
    if (!entry) return;
    const key = entry.dataset.menuEntry;
    if (key === 'planning' && hdrMenuVariant() === 'B') { setSubmenu(true, { focus: true }); return; }
    if (key === 'setup-back') { setOpen(false, { focusButton: true }); actions.planning?.(); return; }
    const action = actions[key] || (key.startsWith('setup:') ? () => options.setupAction?.('select', decodeURIComponent(key.slice(6))) :
      key.startsWith('setup-') ? () => options.setupAction?.(key.slice(6)) : null);
    setOpen(false, { focusButton: true });
    // After this click has finished: a handler that opens its own panel (Account) must not see this click as an outside click.
    if (typeof action === 'function') setTimeout(() => action(), 0);
  });
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && hdrMenuVariant() === 'B' && submenu() && !submenu().hidden) {
      event.preventDefault(); event.stopPropagation(); setSubmenu(false, { focus: true }); return;
    }
    if (hdrMenuVariant() === 'B' && event.key === 'ArrowRight' && document.activeElement?.dataset?.menuEntry === 'planning') {
      event.preventDefault(); setSubmenu(true, { focus: true }); return;
    }
    if (hdrMenuVariant() === 'B' && event.key === 'ArrowLeft' && submenu()?.contains(document.activeElement)) {
      event.preventDefault(); setSubmenu(false, { focus: true }); return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End') return;
    const list = [...panel.querySelectorAll('[data-menu-entry], [data-setup-rename], [data-setup-delete]')]
      .filter((element) => !element.closest('[hidden]'));
    const at = list.indexOf(document.activeElement);
    let next = at;
    if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = list.length - 1;
    else next = (at + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length;
    event.preventDefault();
    list[next]?.focus();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && open && !event.defaultPrevented) { event.preventDefault(); setOpen(false, { focusButton: true }); }
  });
  document.addEventListener('click', (event) => {
    if (open && !panel.contains(event.target) && !button.contains(event.target)) setOpen(false);
  });
  // The phone tier has no Menu: a window that narrows into it closes an open panel.
  window.addEventListener('resize', () => {
    if (open && !button.getClientRects().length) setOpen(false);
    else position();
  });
  syncPlace();
  if (page === 'cards' && typeof MutationObserver === 'function') {
    new MutationObserver(syncPlace).observe(document.body, { attributes: true, attributeFilter: ['data-cb-mode'] });
  }
  return { open: () => setOpen(true), close: () => setOpen(false, { focusButton: true }), isOpen: () => open, syncPlace };
}
