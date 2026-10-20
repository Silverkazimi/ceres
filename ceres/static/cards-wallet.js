// cards-wallet.js -- : the board's wallet on the Cards page.
// The same wallet, not a copy: regional-dec.js (the board's wallet drawer, its tabs, swap / transfer / pool / DEC stake cards
// signing through wallet.js and broadcast-gate.js) is loaded on the first press of a wallet control and mounted with its own
// initRegionalDec(), exactly as on the board. Its module graph reaches board.js (wallet.js reads the page's player from it)
// on this page that player is the URL's ?player=, which card-browser.js always writes, so the wallet is the shown player's.
// The graph is loaded on demand, as the checkout (card-checkout.js) already loads it, so opening /cards still never loads the
// board's modules. This file imports nothing.
// Desktop: the board's right-edge slide-out (#edge-tabs / #wallet-picker), centred on the filter row as app.js places it.
// Phone (<= 1279): the edge slide-out does not exist (as on the board); an icon-only header button opens the full-height drawer.
// the
// wallet stack sits above the Cards dialogs (card-browser.css), and the checkout re-reads its quote when the wallet closes.

const SELECTION_KEY = 'ceres:wallet:resource';  // regional-dec.js WALLET_SELECTION_KEY
// The toggle's icon before the wallet module is loaded: the same files icons.js RESOURCE_ICON names (guarded by a test).
export const CARDS_WALLET_ICON = {
  DEC: 'land/token-dec.png', SPS: 'resources/sps.png', VOUCHER: 'resources/voucher.svg', GRAIN: 'resources/grain.png',
  WOOD: 'resources/wood_200.webp', STONE: 'resources/stone_200.webp', IRON: 'resources/iron_200.webp',
  RESEARCH: 'resources/research.png', AURA: 'resources/aura_200.webp',
};

let mounted = null;

export function walletSelection(storage = globalThis.localStorage) {
  let stored = null;
  try { stored = storage?.getItem(SELECTION_KEY) ?? null; } catch (_) { stored = null; }
  return Object.hasOwn(CARDS_WALLET_ICON, stored) ? stored : 'DEC';
}

// The wallet module, loaded once; every wallet control waits for the same promise.
export function mountWallet() {
  if (!mounted) {
    // wallet.js first, as card-checkout.js enters the graph: wallet.js -> board.js evaluates board.js completely before wallet.js
    // reads the page's player at load. Entering through regional-dec.js would reach wallet.js inside board.js's own import
    // cycle, before board.js has its player (a "before initialization" error).
    mounted = import('./wallet.js').then(() => import('./regional-dec.js')).then((module) => {
      module.initRegionalDec();
      document.body.dataset.cardsWallet = 'ready';
      return module;
    });
    mounted.catch(() => { mounted = null; });
  }
  return mounted;
}

// app.js updateEdgeTabsPosition(): the slide-out fills the filter row's inner box (2px inset top and bottom).
function placeEdgeTabs() {
  const edgeTabs = document.getElementById('edge-tabs');
  const row = document.querySelector('.controls-row');
  const picker = document.getElementById('wallet-picker');
  if (!edgeTabs || !row || !picker) return;
  const rect = row.getBoundingClientRect();
  const top = Math.round(rect.top) + 2;
  const bottom = Math.round(rect.bottom - 1) - 2;
  if (bottom - top > 8) document.documentElement.style.setProperty('--edge-row-h', `${bottom - top}px`);
  const height = picker.getBoundingClientRect().height;
  if (!(rect.height > 0) || !(height > 0)) return;
  edgeTabs.style.top = `${bottom - top > 8 ? top : rect.top + rect.height / 2 - height / 2}px`;
  edgeTabs.classList.add('edge-tabs-revealed');
}

// app.js centerThemePicker(): the slide-out takes its size and padding from the Iris picker's live measurement.
function measureIris() {
  const picker = document.querySelector('.theme-picker');
  const toggle = picker?.querySelector('.theme-toggle');
  const p = picker?.getBoundingClientRect();
  const t = toggle?.getBoundingClientRect();
  if (!p || !t || !(t.width > 0)) return;
  const pcs = getComputedStyle(picker);
  const tcs = getComputedStyle(toggle);
  const px = (value) => Number.parseFloat(value) || 0;
  const closed = t.width + px(pcs.paddingLeft) + px(pcs.paddingRight) + px(pcs.borderLeftWidth) + px(pcs.borderRightWidth);
  if (Math.abs(p.width - closed) > 0.5) return;  // the Iris tray is open: measure next time
  const root = document.documentElement.style;
  root.setProperty('--edge-w', `${Math.round(closed)}px`);
  root.setProperty('--edge-chip-w', `${Math.round(t.width)}px`);
  root.setProperty('--edge-chip-h', `${Math.round(t.height)}px`);
  root.setProperty('--edge-chip-font', tcs.fontSize);
  root.setProperty('--edge-pad-left', pcs.paddingLeft);
  root.setProperty('--edge-pad-right', `${Math.max(0, Math.round(p.right - t.right))}px`);
  root.setProperty('--edge-radius-outer', pcs.borderRadius);
  root.setProperty('--edge-radius-inner', tcs.borderRadius);
  root.setProperty('--edge-chip-shadow', tcs.boxShadow);
}

function layout() { measureIris(); placeEdgeTabs(); }

// First press: load and mount the wallet, then hand the same press to the wallet's own control.
function firstPress(button) {
  if (!button) return;
  const onFirst = async (event) => {
    if (document.body.dataset.cardsWallet === 'ready') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    button.removeEventListener('click', onFirst, true);
    button.setAttribute('aria-busy', 'true');
    try { await mountWallet(); } catch (_) { button.addEventListener('click', onFirst, true); return; } finally { button.removeAttribute('aria-busy'); }
    button.click();
  };
  button.addEventListener('click', onFirst, true);
}

export function initCardsWallet() {
  const toggle = document.getElementById('wallet-edge-tab');
  const headerButton = document.getElementById('regional-dec-button');
  if (!toggle && !headerButton) return;
  const icon = `/static/assets/${CARDS_WALLET_ICON[walletSelection()]}`;
  if (toggle && !toggle.firstChild) toggle.innerHTML = `<img src="${icon}" alt="">`;
  firstPress(toggle);
  firstPress(headerButton);
  requestAnimationFrame(layout);
  window.addEventListener('resize', layout);
  window.addEventListener('scroll', placeEdgeTabs, { passive: true });
  const header = document.querySelector('header.board-header');
  if (header && typeof ResizeObserver === 'function') new ResizeObserver(layout).observe(header);
  // the Iris picker mounts asynchronously: measure again once it is there
  if (typeof MutationObserver === 'function') {
    const dock = document.getElementById('header');
    if (dock) new MutationObserver(layout).observe(dock, { childList: true, subtree: true });
  }
}
