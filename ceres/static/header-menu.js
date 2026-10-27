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
    { key: 'combine', name: 'Find combinables', desc: 'Cards you can combine', icon: 'cards' },
  ] },
  { title: 'Player', entries: [
    { key: 'refresh', name: 'Refresh', desc: 'Reload this player’s data', icon: 'refresh' },
    { key: 'account', name: 'Account', desc: 'Sign in and your keys', icon: 'account' },
  ] },
];

// The page's name beside Menu and the Places entry marked current. Find combinables is a view of My cards ().
export function hdrMenuPlace(page, cbMode) {
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

export function hdrMenuPanelHtml(currentKey) {
  const entry = (item) => `<li><button type="button" class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass" data-control-density="comfortable" data-menu-entry="${item.key}"${item.key === currentKey ? ' aria-current="page"' : ''}>${hdrMenuIcon(item.icon)}<span class="hdr-menu-txt"><span class="hdr-menu-name">${item.name}</span><span class="hdr-menu-desc">${item.desc}</span></span></button></li>`;
  const column = (group, index) => `<div class="hdr-menu-col"><h3 id="hdr-menu-group-${index}">${group.title}</h3><ul aria-labelledby="hdr-menu-group-${index}">${group.entries.map(entry).join('')}</ul></div>`;
  return `<div class="hdr-menu-top"><h2 id="hdr-menu-title">Menu</h2><button type="button" class="harvest-x hdr-menu-close ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-mode="icon" data-menu-close aria-label="Close menu">${HDR_MENU_CLOSE_ICON}</button></div>`
    + `<div class="hdr-menu-cols">${HDR_MENU_GROUPS.map(column).join('')}</div>`;
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
  const position = () => {
    if (!open) return;
    const box = button.getBoundingClientRect();
    const header = button.closest('header')?.getBoundingClientRect();
    // unrounded: the panel's edge lines up with the Menu button's even when the header puts it on a fraction of a pixel
    panel.style.insetInlineStart = `${box.left}px`;
    panel.style.insetBlockStart = `${(header ? header.bottom : box.bottom) + 8}px`;
  };
  const entries = () => [...panel.querySelectorAll('[data-menu-entry]')];
  const setOpen = (next, { focusButton = false } = {}) => {
    if (next === open) return;
    open = next;
    if (open) {
      panel.innerHTML = hdrMenuPanelHtml(current().key);  // fresh icons (the wallet logo follows the chosen wallet)
      panel.hidden = false;
      position();
      (panel.querySelector('[data-menu-entry][aria-current="page"]') || entries()[0])?.focus({ preventScroll: true });
    } else {
      panel.hidden = true;
      if (focusButton) button.focus({ preventScroll: true });
    }
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-pressed', String(open));
  };

  button.addEventListener('click', () => setOpen(!open));
  panel.addEventListener('click', (event) => {
    if (event.target.closest?.('[data-menu-close]')) { setOpen(false, { focusButton: true }); return; }
    const entry = event.target.closest?.('[data-menu-entry]');
    if (!entry) return;
    const action = actions[entry.dataset.menuEntry];
    setOpen(false, { focusButton: true });
    // After this click has finished: a handler that opens its own panel (Account) must not see this click as an outside click.
    if (typeof action === 'function') setTimeout(() => action(), 0);
  });
  panel.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End') return;
    const list = entries();
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
