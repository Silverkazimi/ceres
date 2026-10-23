import { balancesChanged, onBalancesChanged } from './balances-bus.js';
import { CURRENT_PLAYER, BOARD } from './board.js';
import { esc, regionLabel } from './format.js';
import { tokenIcon, tokenLabel } from './harvest-state.js';
import { harvestSuccessHtml } from './harvest-results.js';
import { observeHarvestResourceDividers, syncHarvestResourceDividers } from './harvest-resources.js';
import { DARK_ENERGY_SRC } from './tooltip.js';
import { territorySlug } from './icons.js';
import { walletControlsHtml, walletReady, walletSignLabelHtml } from './wallet.js';
import { requestBroadcastThroughGate } from './broadcast-gate.js';
import { WALLET_TABS, walletTabsHtml, walletResourceHtml } from './wallet-resources.js';
import { claimHtml, claimAction, loadClaimPending } from './wallet-claim.js';
import { isMobileBoardTier } from './table.js';
import { bindCountdowns } from './countdown.js';
import { onIntentChange, resumeSwapChecks, swapHtml, swapAction, swapInput, swapReceive, swapRegion, swapLocked, enhanceSwap, positionHtml, poolInput, poolRegion, transferInput, transferSelect } from './wallet-lp.js';

const D = { player: '', generation: 0, data: null, wallet: null, rewards: null, walletBusy: false, tab: 'DEC', review: null, pending: null, busy: false, signing: false, checking: false, closing: false, error: '', message: '', focus: null, costsOpen: new Set(), claim: { review: null, pending: null, busy: false, checking: false } };
// (a): pure predicate for the tab-switch guard, extracted so it can be tested in isolation.
// (a): a pending DEC receipt or pending claim alone no longer locks the DEC/SPS tabs against
// each other — only D.review/D.claim.review (an open confirmation) or D.busy still block every tab.
export function tabSwitchAllowed(D, tab) {
  if (!WALLET_TABS.includes(tab) || D.review || D.claim.review || D.busy) return false;
  if ((D.pending || D.claim.pending) && !['DEC', 'SPS'].includes(tab)) return false;
  return true;
}
const fmt = value => Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
// a signed DEC change the game does not
// know ends as "not found" once the claims window (, 25 s) has passed since the signature -- never pending
// forever. An entry without a signing time (an older version, or a transaction ID typed in) ends at the first answer.
export const DEC_NOT_FOUND_AFTER_MS = 25000;
export function decNotFoundFinal(pending, lastStatus, now = Date.now()) {
  if (lastStatus !== 'not_found' || !pending) return false;
  const signedAt = Number(pending.signed_at);
  return !Number.isFinite(signedAt) || signedAt <= 0 || now - signedAt >= DEC_NOT_FOUND_AFTER_MS;
}
const stakeOrder = row => Number.isFinite(Number(row.staked)) ? Number(row.staked) : -Infinity;
const account = () => String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
const key = () => `ceres:regional-dec:pending:${D.player}`;
// Canonical codes/names from static/assets/manifest.json territory_colors.
const territories = { PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' };
function regionHeading(uid) {
  const slug = territorySlug(territories[String(uid).split('-')[1]]);
  return `<span class="ch-plot-badge${slug ? ` territory-${slug}` : ''}">${esc(regionLabel(uid))}</span>`;
}
const stakeFigure = value => `<span>${fmt(value)}</span>${Number(value) !== 0 ? `<img src="${esc(DARK_ENERGY_SRC)}" alt="Dark Energy" class="dec-energy-icon">` : ''}`;
const stakeTone = (value, required) => Number(value) >= Number(required) ? 'dec-surplus' : 'dec-shortfall';
// the wallet's account-wide staked DEC total, independent of this tab's per-worksite
// staked/required cards.
function stakedDecHtml(wallet) {
  if (!wallet) return '';
  const staked = wallet.staked_dec;
  const regions = staked?.by_region;
  const tip = regions?.length ? regions.map(r => `${regionLabel(r.region_uid)}: ${fmt(r.amount)}`).join(' · ') : '';
  const stakedValue = staked?.overall != null
    ? `<span class="dec-staked">${fmt(staked.overall)}<img src="${esc(tokenIcon('DEC'))}" alt="DEC" class="dec-energy-icon"></span>`
    : '<span class="wallet-unavailable">unavailable</span>';
  return `<p class="dec-liquid"${tip ? ` data-tip="${esc(tip)}"` : ''}>Staked DEC ${stakedValue}</p>`;
}
let drawer;
let trigger;
let walletPicker, walletTray, walletToggleBtn;
let previewTimer = null;
let previewPacingTimer = null;
let previewInFlight = false;
let previewQueued = null;
let previewLastStartedAt = 0;
const PREVIEW_DEBOUNCE_MS = 300;
const PREVIEW_MIN_INTERVAL_MS = 2000;
const WALLET_SLIDE_DURATION = 500;
const expandedEmptyRegions = new Set();
// (maintainer): "Make the top of the wallet mirror exactly the icon only slide-out
// of Iris." The selected wallet resource is remembered per viewer, mirroring Iris's
// own theme persistence -- reads/writes wrapped in try/catch since a private window
// or blocked site data can throw.
const WALLET_SELECTION_KEY = 'ceres:wallet:resource';
function getWalletSelection() {
  let stored = null;
  try { stored = localStorage.getItem(WALLET_SELECTION_KEY); } catch (_) { stored = null; }
  return WALLET_TABS.includes(stored) ? stored : 'DEC';
}
function updateWalletToggleIcon(symbol) {
  if (walletToggleBtn) walletToggleBtn.innerHTML = `<img src="${esc(tokenIcon(symbol))}" alt="">`;
}
function updateWalletTrayActive(symbol) {
  if (!walletTray) return;
  walletTray.querySelectorAll('.edge-opt').forEach((opt) => {
    const active = opt.dataset.symbol === symbol;
    opt.classList.toggle('edge-opt--active', active);
    opt.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}
// Exported so the drawer's own tab row (act() 'tab:' handling below) keeps the edge
// picker's toggle icon and hidden-in-tray option in sync with a selection made
// INSIDE the drawer, not just one made from the tray itself.
export function setWalletSelection(symbol) {
  if (!WALLET_TABS.includes(symbol)) return;
  try { localStorage.setItem(WALLET_SELECTION_KEY, symbol); } catch (_) { /* ignore */ }
  updateWalletToggleIcon(symbol);
  updateWalletTrayActive(symbol);
}
function walletTransitionSettled(open) {
  const trayWidth = walletTray?.getBoundingClientRect().width ?? 0;
  const trayTarget = open ? (walletTray?.scrollWidth ?? 0) : 0;
  if (Math.abs(trayWidth - trayTarget) > 1) return false;
  const panel = drawer?.querySelector('.dec-drawer-panel');
  if (!panel || !drawer?.open || !isDesktopPanel()) return true;
  const transform = getComputedStyle(panel).transform;
  const offset = transform === 'none' ? 0 : Number.parseFloat(transform.split(',')[4]) || 0;
  const target = open ? 0 : panel.getBoundingClientRect().width;
  return Math.abs(offset - target) <= 1;
}
function waitForWalletTransition(open) {
  if (!walletPicker || !walletTray || !drawer || !isDesktopPanel() || window.matchMedia('(prefers-reduced-motion: reduce)').matches || walletTransitionSettled(open)) return Promise.resolve();
  return new Promise(resolve => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (!walletTransitionSettled(open)) return;
        finished = true;
        walletTray.removeEventListener('transitionend', finish);
        drawer.querySelector('.dec-drawer-panel')?.removeEventListener('transitionend', finish);
        resolve();
      }));
    };
    walletTray.addEventListener('transitionend', finish);
    drawer.querySelector('.dec-drawer-panel')?.addEventListener('transitionend', finish);
    window.setTimeout(finish, WALLET_SLIDE_DURATION + 80);
  });
}
function setWalletSurfaceTarget(open) {
  if (!walletPicker || !drawer || !isDesktopPanel()) return Promise.resolve();
  walletPicker.dataset.open = open ? 'true' : 'false';
  drawer.dataset.walletMotion = open ? 'open' : 'closed';
  const motion = waitForWalletTransition(open).then(() => {
    if (walletPicker.dataset.open === (open ? 'true' : 'false')) walletToggleBtn?.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  return motion;
}
function closeWalletPicker() {
  if (!walletPicker) return Promise.resolve();
  if (drawer?.open && isDesktopPanel()) return close();
  return setWalletSurfaceTarget(false);
}
// the tray and its drawer share one target state and one measured transition.
function toggleWalletPicker() {
  if (!walletPicker) return;
  const open = walletPicker.dataset.open === 'true';
  if (open) {
    if (drawer?.open && isDesktopPanel()) close();
    else closeWalletPicker();
    walletToggleBtn?.focus({ preventScroll: true });
    return;
  }
  openWalletDrawer(getWalletSelection());
}
// the wallet is a floating panel anchored under the edge tray on desktop
// (the tray is the only resource picker there); mobile keeps the full-height drawer
// with its own header/Close/tab row, unchanged.
function isDesktopPanel() { return !isMobileBoardTier(); }
export function desktopPanelClickShouldClose({ pointerDownInside, targetConnected, panelContains, pickerContains }) {
  return targetConnected && !pointerDownInside && !panelContains && !pickerContains;
}
// Team-lead correction (2026-09-19): #wallet-picker's own position is not a fixed
// CSS-var-derivable constant -- a later version moves it onto the filter row, where
// it scrolls with the page -- so the panel is positioned straight off the picker's
// own LIVE rect instead, every time this runs (called on open, and on every resize
// scroll/`ceres:table-layout` while the panel stays open -- see initRegionalDec()).
function positionDesktopPanel() {
  if (!drawer || !drawer.open) return;
  if (!isDesktopPanel()) {
    // a desktop->mobile resize while the panel stays OPEN (not closed and
    // reopened) still runs this on the 'resize' listener -- clear any inline top
    // right/maxHeight left from the desktop position so the full-height mobile
    // drawer isn't offset/clipped by it (close() clears the same styles for the
    // close-then-reopen path; this covers the stay-open path).
    drawer.style.top = '';
    drawer.style.right = '';
    drawer.style.maxHeight = '';
    return;
  }
  const picker = document.getElementById('wallet-picker');
  if (!picker) return;
  const rect = picker.getBoundingClientRect(); // V
  const filterRow = document.querySelector('.controls-row');
  const filterRect = filterRow?.getBoundingClientRect(); // V
  const viewportRight = document.documentElement.getBoundingClientRect().right; // V
  const top = Math.round(filterRect?.bottom ?? rect.bottom);
  const right = Math.round(Math.max(0, viewportRight - rect.right));
  drawer.style.top = `${top}px`;
  drawer.style.right = `${right}px`;
  drawer.style.maxHeight = `calc(100vh - ${top}px - 1rem)`;
}
// any control that opens the wallet drawer DIRECTLY carries
// aria-controls="regional-dec-drawer" and stays in sync with its real open/closed
// state -- today that is only the mobile #regional-dec-button. Pre-landing review fix
// (): #wallet-edge-tab no longer qualifies -- it is the edge picker's own
// TOGGLE (aria-controls="wallet-picker-tray", the tray it opens/closes), not a direct
// drawer trigger; its own expanded state is owned by toggleWalletPicker() instead.
function setWalletTriggersExpanded(open) {
  document.querySelectorAll('[aria-controls="regional-dec-drawer"]').forEach((el) => el.setAttribute('aria-expanded', open ? 'true' : 'false'));
}
const button = (action, label, primary = false, disabled = false, extra = '') => `<button type="button" class="ceres-btn ceres-btn--${primary ? 'primary' : 'secondary'}" data-control-density="touch" data-dec="${action}"${extra}${disabled ? ' disabled' : ''}>${label}</button>`;

async function api(path, body, options = {}) {
  const response = await fetch(`/api/dec${path}?player=${encodeURIComponent(options.player || D.player)}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: options.signal } : { signal: options.signal });
  const result = await response.json();
  if (!response.ok) { const error = new Error(typeof result.detail === 'string' ? result.detail : 'DEC request failed. Please retry.'); error.status = response.status; throw error; }
  return result;
}
const validStakeAmount = amount => /^[0-9]{1,12}(?:\.[0-9]{1,3})?$/.test(amount) && Number(amount) > 0;
function cancelStakePreview() {
  if (previewTimer != null) clearTimeout(previewTimer);
  previewTimer = null;
  if (D.review && previewQueued?.review === D.review) previewQueued = null;
  if (D.review) {
    D.review.previewRequest = (D.review.previewRequest || 0) + 1;
    D.review.previewing = false;
    D.review.preview = null;
    D.review.previewAmount = null;
    D.review.lastPreviewedAmount = null;
    D.review.requestedPreviewAmount = null;
    D.review.shortfallConfirmed = false;
  }
}
function pumpStakePreview() {
  if (previewInFlight || !previewQueued) return;
  const wait = PREVIEW_MIN_INTERVAL_MS - (Date.now() - previewLastStartedAt);
  if (wait > 0) {
    if (previewPacingTimer == null) previewPacingTimer = setTimeout(() => {
      previewPacingTimer = null;
      pumpStakePreview();
    }, wait);
    return;
  }
  const job = previewQueued;
  previewQueued = null;
  previewInFlight = true;
  previewLastStartedAt = Date.now();
  api('/preview', { region_uid: job.review.region_uid, action: job.review.action, amount: job.amount })
    .then(preview => job.onSuccess(preview))
    .catch(error => job.onError(error))
    .finally(() => {
      previewInFlight = false;
      pumpStakePreview();
    });
}
function enqueueStakePreview(job) {
  if (previewQueued?.resolve) previewQueued.resolve(null);
  previewQueued = job;
  pumpStakePreview();
}
function requestFreshStakePreview(review, amount) {
  return new Promise((resolve, reject) => enqueueStakePreview({
    review, amount, resolve,
    onSuccess: resolve,
    onError: reject,
  }));
}
function queueStakePreview() {
  if (previewTimer != null) clearTimeout(previewTimer);
  previewTimer = null;
  const review = D.review;
  if (!review) return;
  const amount = String(review.amount || '').trim();
  if (validStakeAmount(amount) && review.preview && review.lastPreviewedAmount === amount && review.previewAmount === amount) return;
  if (validStakeAmount(amount) && review.previewing && review.requestedPreviewAmount === amount) return;
  if (previewQueued?.review === review) previewQueued = null;
  review.preview = null;
  review.previewAmount = null;
  review.lastPreviewedAmount = null;
  review.requestedPreviewAmount = null;
  review.shortfallConfirmed = false;
  review.previewRequest = (review.previewRequest || 0) + 1;
  const request = review.previewRequest;
  if (!validStakeAmount(amount)) {
    review.previewing = false;
    if (previewQueued?.review === review) previewQueued = null;
    return;
  }
  review.previewing = true;
  review.requestedPreviewAmount = amount;
  previewTimer = setTimeout(() => {
    previewTimer = null;
    if (D.review !== review || review.previewRequest !== request) return;
    if (review.amount.trim() !== amount || account() !== D.player) {
      review.previewing = false;
      render();
      return;
    }
    const finish = () => {
      if (D.review === review && review.previewRequest === request) {
        review.previewing = false;
        review.requestedPreviewAmount = null;
        render();
      }
    };
    enqueueStakePreview({
      review, amount,
      onSuccess: preview => {
        if (D.review === review && review.previewRequest === request && review.amount.trim() === amount && account() === D.player) {
          review.preview = preview;
          review.previewAmount = amount;
          review.lastPreviewedAmount = amount;
        }
        finish();
      },
      onError: error => {
        if (D.review === review && review.previewRequest === request && review.amount.trim() === amount) D.error = error.message;
        finish();
      },
    });
  }, PREVIEW_DEBOUNCE_MS);
}
async function withDecLock(work) {
  const locks = globalThis.navigator?.locks;
  if (locks?.request) return locks.request(`ceres:regional-dec:${String(D.player).toLowerCase()}`, async () => work());
  return work();
}
function savePending(value) {
  D.costsOpen = new Set();  // a new or cleared receipt starts with its costs closed
  if (value) localStorage.setItem(key(), JSON.stringify(value));
  else localStorage.removeItem(key());
  D.pending = value;
}
function harvestReceiptHtml(harvest) {
  const efficiency = [harvest?.pre_op_efficiency, harvest?.post_op_efficiency];
  const efficiencyHtml = efficiency.every(value => typeof value === 'number' && Number.isFinite(value))
    ? `<p>Regional efficiency: <strong>${fmt(efficiency[0] * 100)}% → ${fmt(efficiency[1] * 100)}%</strong></p>` : '';
  const state = harvest?.state;
  const content = state === 'not_triggered' ? '<h3>No harvest triggered</h3><p>The game did not harvest this region with this DEC change. Its uncollected production remains available.</p>'
    // the forced harvest is the Harvest all receipt
    // the ledger wrapper gives it that receipt's ledger styling (ceres.css :is(#harvest-modal-overlay, .harvest-receipt-ledger)).
    : state === 'recorded' ? `<div class="harvest-receipt-ledger">${harvestSuccessHtml({ members: harvest.members }, { costsOpen: D.costsOpen })}</div>`
    : state === 'empty' ? '<h3>No plots harvested</h3><p>The game completed the harvest check without returning any plot collections.</p>'
    : state === 'failed' ? `<h3>Harvest not completed</h3><p>${esc(harvest.message)}</p>`
    : '<h3>Harvest details unavailable</h3><p>The DEC change is confirmed, but its harvest outcome is not available in this receipt.</p>';
  return `<section class="dec-harvest-receipt">${efficiencyHtml}${content}${harvest?.details ? `<details><summary>Game harvest details</summary><pre>${esc(JSON.stringify(harvest.details, null, 2))}</pre></details>` : ''}</section>`;
}
function render(force = false) {
  if (!drawer || (!drawer.open && !force)) return;
  const data = D.data;
  const rows = [...(data?.regions || [])].sort((a, b) => stakeOrder(b) - stakeOrder(a) || a.region_uid.localeCompare(b.region_uid, undefined, { numeric: true })).map(row => {
    const collapsedRegion = !row.error && row.staked != null && Number(row.staked) === 0;
    const tag = collapsedRegion ? 'details' : 'article';
    const heading = collapsedRegion ? `<summary class="dec-region-summary">${regionHeading(row.region_uid)}<span class="dec-region-empty-label">No DEC staked</span><span class="dec-region-chevron ceres-chevron" aria-hidden="true"></span></summary>` : `<h3>${regionHeading(row.region_uid)}</h3>`;
    return `<${tag} class="dec-region${collapsedRegion ? ' dec-region-collapsible' : ''}" data-region="${esc(row.region_uid)}"${collapsedRegion && expandedEmptyRegions.has(row.region_uid) ? ' open' : ''}>
    ${heading}
    ${row.error ? `<p class="dec-error">${esc(row.error)}</p>` : `<dl class="dec-figures"><div><dt>Staked</dt><dd class="dec-staked ${stakeTone(row.staked, row.required)}">${stakeFigure(row.staked)}</dd></div><div><dt>Required</dt><dd class="dec-staked">${stakeFigure(row.required)}</dd></div><div class="${row.shortfall ? 'dec-shortfall' : 'dec-surplus'}"><dt>${row.shortfall ? 'Shortfall' : 'Surplus'}</dt><dd class="dec-staked">${stakeFigure(row.shortfall || row.surplus)}</dd></div></dl>
    <div class="dec-region-actions">${button('stake', 'Stake', true, D.busy || !!D.pending)}${button('unstake', 'Unstake', false, D.busy || !!D.pending || !row.staked)}</div>`}</${tag}>`;
  }).join('');
  const review = D.review;
  let content = '';
  if (D.pending) {
    const pending = D.pending;
    const confirmed = pending.status === 'confirmed';
    const failed = pending.status === 'failed';
    const notFound = pending.status === 'not_found';
    const terminal = confirmed || failed;
    content = notFound ? `<section class="dec-review dec-receipt is-failed" aria-live="polite">
      <header><h3>Transaction not found</h3>${regionHeading(pending.region_uid)}</header>
      <div class="dec-receipt-amount"><img src="${esc(tokenIcon('DEC'))}" alt="DEC"><div><span>${pending.action === 'stake' ? 'Stake' : 'Unstake'}</span><strong>${fmt(pending.amount)} <small>DEC</small></strong></div></div>
      <p>The game has no record of this transaction, so your regional DEC did not change. It was most likely never broadcast. Dismiss to continue; if your wallet history shows it, Check again reads the game once more — nothing is signed again.</p>
      <div class="dec-receipt-actions">${pending.tx_id ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(pending.tx_id)}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}${button('dismiss', 'Dismiss', true, D.busy)}${pending.tx_id ? button('verify', 'Check again', false, D.busy) : ''}</div>
    </section>` : `<section class="dec-review dec-receipt${confirmed ? ' is-confirmed' : failed ? ' is-failed' : ' is-waiting'}" aria-live="polite">
      <header><h3>${confirmed ? 'DEC change confirmed' : failed ? 'DEC change rejected' : 'Confirming DEC change'}</h3>${regionHeading(pending.region_uid)}</header>
      <div class="dec-receipt-amount"><img src="${esc(tokenIcon('DEC'))}" alt="DEC"><div><span>${pending.action === 'stake' ? (confirmed ? 'Staked' : 'Stake') : (confirmed ? 'Unstaked' : 'Unstake')}</span><strong>${fmt(pending.amount)} <small>DEC</small></strong></div></div>
      <p>${confirmed ? 'Your regional DEC stake has been updated.' : failed ? esc(pending.message || 'The game rejected this change.') : D.checking ? 'Waiting for the game to confirm your transaction…' : 'Confirmation is still pending. Checking again will not sign another transaction.'}</p>
      ${confirmed ? harvestReceiptHtml(pending.harvest) : ''}
      ${!pending.tx_id ? `<p>The wallet has not returned a transaction ID. Check your wallet history before continuing.</p><label class="dec-amount">Transaction ID<input id="dec-transaction" type="text" autocomplete="off" spellcheck="false"></label>` : ''}
      <div class="dec-receipt-actions">${pending.tx_id ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(pending.tx_id)}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}
      ${terminal ? button('done', 'Done', true, D.busy) : pending.tx_id ? `${button('verify', 'Check again', true, D.busy)}${!D.checking ? button('change-tx', 'Change transaction ID', false, D.busy) : ''}` : `${button('recover', 'Check transaction', true, D.busy)}${button('discard', 'Discard this change', false, D.busy)}`}</div>
      ${!pending.tx_id ? '<p class="wallet-meta">Discard only if your wallet shows no broadcast for this change; one that did go through changes your stake either way.</p>' : ''}
    </section>`;
  } else if (review) {
    const row = data.regions.find(r => r.region_uid === review.region_uid);
    const preview = review.preview;
    content = `<section class="dec-review"><h3>${review.action === 'stake' ? 'Stake DEC' : 'Unstake DEC'}</h3>${regionHeading(review.region_uid)}
      <dl class="dec-figures"><div><dt>Staked</dt><dd class="dec-staked ${stakeTone(row.staked, row.required)}">${stakeFigure(row.staked)}</dd></div><div><dt>Required</dt><dd class="dec-staked">${stakeFigure(row.required)}</dd></div><div class="${row.shortfall ? 'dec-shortfall' : 'dec-surplus'}"><dt>${row.shortfall ? 'Missing' : 'Surplus'}</dt><dd class="dec-staked">${stakeFigure(row.shortfall || row.surplus)}</dd></div></dl>
      <label class="dec-amount">Amount in DEC<input id="dec-amount" type="text" inputmode="decimal" autocomplete="off" value="${esc(review.amount)}"${D.busy ? ' disabled' : ''}></label>
      <div class="dec-shortcuts">${button('needed', review.action === 'stake' ? 'Fill shortfall' : 'Use surplus', false, D.busy || !(review.action === 'stake' ? row.shortfall : row.surplus))}${button('maximum', 'Maximum', false, D.busy)}</div>
      <p class="dec-warning">Changing regional production efficiency can trigger a harvest. Changes within your surplus can leave production uncollected.</p>
      ${review.previewing ? '<p class="dec-status" role="status"><span class="spinner" aria-hidden="true"></span>Updating live preview…</p>' : ''}
      ${preview ? `<dl class="dec-figures"><div><dt>Staked after</dt><dd class="dec-staked ${stakeTone(preview.after, preview.region.required)}">${stakeFigure(preview.after)}</dd></div><div><dt>Required</dt><dd class="dec-staked">${stakeFigure(preview.region.required)}</dd></div></dl>${preview.shortfall_after ? `<p class="dec-error" role="alert"><span class="dec-staked">${stakeFigure(preview.shortfall_after)}</span> below requirement after this change. Worker production may decrease.</p>` : ''}<details><summary>Show Hive operation</summary><pre>${esc(JSON.stringify(preview.operations, null, 2))}</pre></details>` : ''}
      <div class="dec-region-actions">${preview && review.previewAmount === review.amount ? preview.shortfall_after && !review.shortfallConfirmed ? button('confirm-shortfall', 'Continue despite worker shortfall', false, D.busy || review.previewing, ' data-control-case="destructive"')  /* a risky confirmation uses the shared destructive style */ : `<span class="wallet-sign-pair">${walletControlsHtml(D.player)}${button('sign', walletSignLabelHtml(), true, D.busy || review.previewing || !preview.signing_enabled || !walletReady(D.player))}</span>` : ''}</div>
      ${preview && !preview.signing_enabled ? '<p>Signing is disabled on this server.</p>' : ''}</section>`;
  } else content = `<p class="dec-liquid">Available DEC <strong class="dec-staked">${data ? fmt(data.balance) : '—'}<img src="${esc(tokenIcon('DEC'))}" alt="DEC" class="dec-energy-icon"></strong></p>${stakedDecHtml(D.wallet)}${rows || (!D.busy ? '<p>No regions with land or staked DEC.</p>' : '')}`;
  // the DEC tab keeps this drawer's staking content; every other tab renders the wallet-resources view.
  // the swap card closes every swappable tab; on SPS it follows the claim button.
  if (D.tab !== 'DEC') content = withClaimInRewards(walletResourceHtml(D.tab, D.wallet, D.walletBusy), D.tab === 'SPS' ? claimHtml(D) : '') + (D.walletBusy ? '' : swapHtml(D.tab, D.wallet, D.player) + positionHtml(D.tab, D.wallet, D.player));  // the pool position closes the tab
  const walletIssues = D.tab !== 'DEC' && D.wallet?.issues?.length ? `<p class="dec-error" role="alert">${esc(D.wallet.issues.join(' '))}</p>` : '';
  // a transaction id being typed survives the re-render (wallet/rewards reads finish while the user pastes).
  const typed = Object.fromEntries(['dec-transaction', 'claim-transaction'].map(id => [id, drawer.querySelector('#' + id)?.value ?? null]));
  // a swap amount being typed keeps its focus and caret across a re-render (its value lives in page memory).
  const focusAttr = ['data-lp-receive', 'data-lp-amount', 'data-lp-pool-amount', 'data-lp-pool-pct', 'data-lp-pool-dec', 'data-lp-transfer-to', 'data-lp-transfer-amount'].find(attr => document.activeElement?.hasAttribute?.(attr));
  const swapFocus = focusAttr && drawer.contains(document.activeElement)
    ? { selector: `[${focusAttr}="${document.activeElement.getAttribute(focusAttr)}"]`,
        caret: [document.activeElement.selectionStart, document.activeElement.selectionEnd] } : null;
  // (a): while only a pending DEC receipt/claim blocks tab switching (not busy/signing/review)
  // the DEC/SPS tabs stay enabled so the user can move between them; every other tab still locks.
  const tabsBusy = D.busy || D.signing || !!D.pending || !!D.review || !!D.claim.review || !!D.claim.pending || !!swapLocked(D.player);
  const tabsAllowed = (D.pending || D.claim.pending) && !D.review && !D.claim.review && !D.busy && !D.signing ? ['DEC', 'SPS'] : undefined;
  // (maintainer): on desktop the edge tray is the only resource picker -- the
  // drawer's own header (title + Close) and icon tab row are dropped there, replaced
  // by a slim caption line naming the open resource; mobile (the full-height
  // #regional-dec-button path) keeps both exactly as before.
  const desktop = isDesktopPanel();
  const forcedPending = D.tab === 'DEC' ? D.pending?.status === 'pending' : D.tab === 'SPS' ? (D.signing && !!D.claim.pending) : false; // only while actually signing
  const headerHtml = desktop ? '' : `<header class="dec-drawer-header"><h2 id="dec-title"><img src="${esc(tokenIcon(D.tab))}" alt="">${D.tab === 'DEC' ? 'Regional DEC' : 'Wallet'}</h2>${button('close', 'Close', false, D.signing)}</header>`;
  const tabsHtml = desktop ? '' : walletTabsHtml(D.tab, tabsBusy, tabsAllowed);
  const captionHtml = desktop ? `<div class="wallet-caption" id="dec-title"><img src="${esc(tokenIcon(D.tab))}" alt=""><span>${esc(D.tab === 'DEC' || D.tab === 'SPS' ? D.tab : tokenLabel(D.tab))}</span>${forcedPending ? '<span class="wallet-caption-pending">Pending signature</span>' : ''}</div>` : '';
  // a receipt on screen before and after this redraw stays where it was -- the new
  // content's scroll is set so its receipt box keeps the same top (a plain redraw reset the scroll to 0 and the box moved).
  const anchorRect = drawer.open ? drawer.querySelector('.dec-drawer-content .dec-receipt')?.getBoundingClientRect() : null;
  const anchorBefore = anchorRect?.height > 0 ? anchorRect.top : null;  // a closed or hidden drawer has no place to keep
  const scrollBefore = drawer.querySelector('.dec-drawer-content')?.scrollTop ?? 0;
  drawer.querySelector('.dec-drawer-panel').innerHTML = `${headerHtml}${tabsHtml}${captionHtml}<div class="dec-drawer-content">${walletIssues}${(D.busy && D.tab === 'DEC' && !D.pending) || (D.walletBusy && D.tab !== 'DEC') ? `<p class="dec-status" role="status"><span class="spinner" aria-hidden="true"></span>${D.checking ? 'Confirming with the game…' : 'Working…'}</p>` : ''}${D.error ? `<p class="dec-error" role="alert">${esc(D.error)}</p>` : ''}${D.message ? `<p role="status">${esc(D.message)}</p>` : ''}${content}</div><footer class="dec-drawer-footer">${!review && !D.pending ? button('refresh', 'Refresh', false, D.busy) : ''}<span>${esc(D.player)}</span></footer>`;
  const panel = drawer.querySelector('.dec-drawer-panel');
  const anchorContent = panel.querySelector('.dec-drawer-content');
  const anchorAfter = anchorBefore != null && anchorContent?.querySelector('.dec-receipt');
  if (anchorAfter) {
    // A shorter receipt (confirmed after waiting) would end the scroll range above the needed position and pull the box down
    // the missing range is held as space below the content, only while this receipt keeps its place (each redraw re-measures).
    anchorContent.scrollTop = 0;
    const wanted = Math.round(anchorAfter.getBoundingClientRect().top - anchorBefore);
    const range = anchorContent.scrollHeight - anchorContent.clientHeight;
    if (wanted > range && scrollBefore > 0) {
      const space = document.createElement('div');
      space.className = 'dec-anchor-space';
      space.setAttribute('aria-hidden', 'true');
      space.style.blockSize = `${wanted - range}px`;
      anchorContent.append(space);
      const short = wanted - (anchorContent.scrollHeight - anchorContent.clientHeight);  // content shorter than the box: measure again
      if (short > 0) space.style.blockSize = `${wanted - range + short}px`;
    }
    anchorContent.scrollTop = Math.max(0, wanted);
  }
  syncHarvestResourceDividers(panel);
  observeHarvestResourceDividers(panel);
  enhanceSwap(panel);
  if (D.player && !D.busy) Promise.resolve().then(() => resumeSwapChecks(swapContext()));
  bindCountdowns();  // the claim timer ticks while the drawer is open // : an open swap confirmation runs by itself, also after a reload
  panel.querySelector?.('.wallet-tabs--icons [aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'center' }); // the open resource stays in view in the phone slider
  for (const [id, value] of Object.entries(typed)) { const input = value != null && drawer.querySelector('#' + id); if (input && !input.value) input.value = value; }
  const swapInputEl = swapFocus && drawer.querySelector(swapFocus.selector);
  if (swapInputEl) { swapInputEl.focus({ preventScroll: true }); try { swapInputEl.setSelectionRange(...swapFocus.caret); } catch (_) { /* no caret */ } }
}
// what the swap card (wallet-lp.js) may use of this drawer. A full re-render keeps the content's
// scroll position and brings the swap card into view, so Review/receipt never land off-screen.
onIntentChange((player) => {  // a swap changed in another tab: draw the drawer again so no stale button stays on screen
  if (drawer?.open && D.player && String(D.player).toLowerCase() === String(player).toLowerCase() && !D.busy) swapContext().render();
});
function swapContext() {
  return {
    player: D.player, account, drawer: () => drawer, wallet: () => D.wallet,
    // `focus` names the card being worked on -- 'pool' keeps the pool box in view, 'pool-top' shows its top
    // (Add / Withdraw opened); otherwise the first swap card or receipt, as before.
    render: (focus) => {
      const top = drawer.querySelector('.dec-drawer-content')?.scrollTop ?? 0;
      render();
      const content = drawer.querySelector('.dec-drawer-content');
      if (!content) return;
      content.scrollTop = top;
      // while a pool add/withdraw is signing, confirming or showing its result, the pool box keeps the focus.
      const pool = (focus && content.querySelector('.wallet-pool')) || content.querySelector('.wallet-pool[data-lp-active]');
      if (pool) pool.scrollIntoView({ block: focus === 'pool-top' ? 'start' : 'nearest' });
      else content.querySelector('.wallet-swap')?.scrollIntoView({ block: 'nearest' });
    },
    // an outcome's balance re-read is quiet -- the receipt box stays where it is while the new figures load
    // (a busy re-read dropped the swap card and its receipt for a frame or more: the empty jump the maintainer saw).
    reload: () => loadWallet({ quiet: true }),
    setSigning: value => { D.signing = value; },
  };
}
async function loadWallet({ quiet = false } = {}) {
  const generation = D.generation;
  // quiet (an outcome's refresh with figures already shown) keeps the content; only a first read shows the spinner.
  if (!(quiet && D.wallet)) { D.walletBusy = true; render(); }
  try {
    const response = await fetch(`/api/wallet?player=${encodeURIComponent(D.player)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Wallet request failed. Please retry.');
    if (generation === D.generation && result.player === D.player && account() === D.player) D.wallet = result;
    if (D.wallet) { D.wallet.rewards = D.rewards; D.wallet.rewardsBusy = D.rewards == null; }
  } catch (error) { if (generation === D.generation) D.wallet = { issues: [error.message], account: null, regions: null, swap: null, soulbound: ['AURA', 'RESEARCH'], resources: ['GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'] }; }
  finally { if (generation === D.generation) { D.walletBusy = false; render(); } }
}
// every claim state occupies the same rewards card, including review and result.
function withClaimInRewards(resourceHtml, claim) {
  if (claim && resourceHtml.endsWith('</section>')) {
    const flow = claim.includes('wallet-claim-flow');
    const lastCard = resourceHtml.lastIndexOf('<section class="wallet-card');
    const tone = claim.includes('wallet-claim-flow is-done') ? ' is-confirmed' : claim.includes('wallet-claim-flow is-failed') ? ' is-failed' : '';
    const html = flow && lastCard >= 0 ? resourceHtml.slice(0, lastCard) + resourceHtml.slice(lastCard)
      .replace('<section class="wallet-card', `<section class="wallet-card${tone}`)
      .replace(/<h3>[\s\S]*?<\/h3>/, '<h3>SPS rewards</h3>') : resourceHtml;
    return html.slice(0, -'</section>'.length) + claim + '</section>';
  }
  if (claim) return `<section class="wallet-card"><h3>SPS rewards</h3>${claim}</section>`;
  return resourceHtml + claim;
}
// when the claim cooldown ends, the rewards are read again so the Claim button opens by itself.
window.addEventListener('ceres:wallet-countdown-ended', () => { if (drawer?.open && D.player && !D.busy) loadRewards().then(() => render()).catch(() => {}); });
async function loadRewards() {
  // read side: independent of the wallet read; failure only marks the rewards card unavailable.
  const generation = D.generation;
  if (D.wallet) D.wallet.rewardsBusy = true;
  try {
    const response = await fetch(`/api/wallet/rewards?player=${encodeURIComponent(D.player)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Reward request failed.');
    if (generation === D.generation && result.player === D.player && account() === D.player) D.rewards = result;
  } catch (_) { if (generation === D.generation) D.rewards = null; }
  finally { if (generation === D.generation) { if (D.wallet) { D.wallet.rewards = D.rewards; D.wallet.rewardsBusy = false; } render(); } }
}
async function load() {
  const generation = ++D.generation;
  D.busy = true; D.error = ''; render();
  loadWallet(); loadRewards();
  try {
    const data = await api('');
    if (generation === D.generation && data.player === D.player && account() === D.player) D.data = data;
  } catch (error) { if (generation === D.generation) D.error = error.message; }
  finally { if (generation === D.generation) { D.busy = false; render(); } }
}
async function close() {
  if (D.signing || D.closing || !drawer.open) return;
  D.closing = true;
  cancelStakePreview();
  ++D.generation; D.busy = false; D.checking = false;
  if (isDesktopPanel()) {
    await setWalletSurfaceTarget(false);
    drawer.close();
  } else if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const animation = drawer.querySelector('.dec-drawer-panel').animate([{ transform: 'translateX(0)' }, { transform: 'translateX(100%)' }], { duration: 240, easing: 'cubic-bezier(.4, 0, .2, 1)', fill: 'forwards' });
    try { await animation.finished; } catch (_) { /* Closing still completes if animation is interrupted. */ }
    drawer.close(); animation.cancel();
  } else drawer.close();
  // positionDesktopPanel() leaves inline top/right/maxHeight on the dialog
  // while it is open as the desktop panel -- clear them here so a desktop->mobile
  // resize before the next open never inherits a stale desktop position on the
  // full-height mobile drawer (positionDesktopPanel() itself already never writes
  // them on the mobile path -- see its own `!isDesktopPanel()` guard).
  drawer.style.top = '';
  drawer.style.right = '';
  drawer.style.maxHeight = '';
  D.closing = false; D.focus?.focus();
  setWalletTriggersExpanded(false);
  // a surface the wallet sat over (the Cards checkout) re-reads its balances once the wallet is closed.
  window.dispatchEvent(new CustomEvent('ceres:wallet-closed', { detail: { player: D.player } }));
}
async function check() {
  const generation = D.generation;
  const player = D.player;
  const intent = { ...D.pending };
  const current = () => generation === D.generation && drawer.open && player === D.player && account() === player && D.pending?.nonce === intent.nonce && D.pending?.tx_id === intent.tx_id;
  const deadline = Date.now() + 25000;
  D.checking = true; D.message = ''; render();
  let last = null;
  try {
    for (const delay of [0, 1000, 2000, 4000]) {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (!current()) return;
      if (Date.now() >= deadline) break;
      try {
        const result = await api('/verify', intent, { player, signal: AbortSignal.timeout(Math.min(12000, Math.max(1, deadline - Date.now()))) });
        if (!current()) return;
        last = result.status;
        if (result.status === 'confirmed' || result.status === 'failed') {
          savePending({ ...intent, status: result.status, message: result.message || '', harvest: result.harvest || null });
          if (result.status === 'confirmed') balancesChanged({ player, assets: ['DEC', 'GRAIN', 'WOOD', 'STONE', 'IRON'], regions: [intent.region_uid], id: `regional-dec:${intent.tx_id}` });
          return;
        }
      } catch (error) {
        if (!current()) return;
        if (error.status && error.status < 500 && ![408, 429].includes(error.status)) throw error;
      }
    }
  } finally {
    if (generation === D.generation) D.checking = false;
  }
  // the game answered "not found" and the window since the signature has passed -- end it clearly.
  if (current() && decNotFoundFinal(intent, last)) savePending({ ...intent, status: 'not_found' });
}
async function act(action, target) {
  if (action === 'close') { close(); return; }
  if (D.busy || D.closing) return;
  if (account() !== D.player) { close(); return; }
  if (action.startsWith('claim:')) { await claimAction(D, action.slice(6), render, account, () => Promise.all([loadWallet({ quiet: true }), loadRewards()])); return; }
  if (action.startsWith('lp:')) { await swapAction(swapContext(), action.slice(3), target); return; }
  D.error = ''; D.message = '';
  if (action === 'close') { close(); return; }
  // (a): a pending DEC receipt or pending claim alone no longer locks the DEC/SPS tabs against
  // each other — only D.review/D.claim.review (an open confirmation) or D.busy still block every tab.
  if (action.startsWith('tab:')) {
    const tab = action.slice(4);
    if (!tabSwitchAllowed(D, tab) || swapLocked(D.player)) return;
    D.tab = tab; setWalletSelection(tab); render(); return;
  }
  if (action === 'refresh') { await load(); return; }
  if (action === 'change-tx') { const pending = { ...D.pending }; delete pending.tx_id; savePending(pending); render(); return; }
  if (action === 'done') { savePending(null); D.review = null; await load(); return; }
  // every pending DEC entry has an exit -- a not-found transaction is dismissed; one without a transaction ID
  // can be discarded.
  if (action === 'dismiss' && D.pending?.status === 'not_found') { savePending(null); D.review = null; await load(); return; }
  if (action === 'discard' && D.pending && !D.pending.tx_id) { savePending(null); D.review = null; render(); return; }
  if (action === 'back') { cancelStakePreview(); D.review = null; render(); return; }
  if (action === 'stake' || action === 'unstake') {
    cancelStakePreview();
    D.review = { region_uid: target.closest('[data-region]').dataset.region, action, amount: '' };
    render(); drawer.querySelector('#dec-amount')?.focus(); return;
  }
  if (action === 'needed' || action === 'maximum') {
    const row = D.data.regions.find(r => r.region_uid === D.review.region_uid);
    D.review.amount = String(action === 'needed' ? (D.review.action === 'stake' ? Math.min(row.shortfall, D.data.balance) : row.surplus) : (D.review.action === 'stake' ? D.data.balance : row.staked));
    D.error = ''; queueStakePreview(); render(); drawer.querySelector('#dec-amount')?.focus(); return;
  }
  if (action === 'confirm-shortfall' || action === 'sign') {
    const amount = drawer.querySelector('#dec-amount')?.value.trim() || '';
    if (!validStakeAmount(amount)) {
      D.error = 'Enter a positive DEC amount with at most three decimal places.'; render(); return;
    }
    if (amount !== D.review.previewAmount || !D.review.preview || D.review.previewing) {
      D.review.amount = amount;
      D.error = 'Wait for the live preview of this amount before continuing.';
      queueStakePreview(); render(); return;
    }
    if (action === 'confirm-shortfall') {
      if (!D.review.preview.shortfall_after) return;
      D.review.shortfallConfirmed = true; D.error = ''; render(); return;
    }
    if (D.review.preview.shortfall_after && !D.review.shortfallConfirmed) {
      D.error = 'Confirm the worker production shortfall before signing.'; render(); return;
    }
    D.review.amount = amount;
  }
  const recoveryTx = drawer.querySelector('#dec-transaction')?.value.trim();
  const generation = D.generation;
  D.busy = true; D.signing = action === 'sign'; render();
  try {
    if (action === 'verify' || action === 'recover') {
      if (action === 'recover') {
        if (!/^[a-f0-9]{40}$/i.test(recoveryTx)) throw new Error('Enter the 40-character transaction ID from your wallet history.');
        savePending({ ...D.pending, tx_id: recoveryTx });
      }
      await check();
    }
    if (action === 'sign') {
      const previous = D.review.preview;
      const fresh = await requestFreshStakePreview(D.review, D.review.amount);
      if (account() !== D.player) throw new Error('Player changed. Close and reopen DEC.');
      if (!fresh.signing_enabled || !walletReady(D.player)) throw new Error('Connect a wallet before signing.');
      if (fresh.balance !== previous.balance || fresh.region.staked !== previous.region.staked || fresh.region.required !== previous.region.required) {
        D.review.preview = fresh;
        D.review.previewAmount = D.review.amount;
        D.review.shortfallConfirmed = false;
        throw new Error('DEC figures changed. Review the updated amounts before signing.');
      }
      const intent = { region_uid: D.review.region_uid, action: D.review.action, amount: D.review.amount, nonce: fresh.nonce, status: 'pending' };
      // S10 / A-04: read-and-reserve the account's pending DEC change in one locked step, so a second open tab never reaches the wallet
      // while the first one's outcome is unresolved.
      const reserved = await withDecLock(() => {
        let stored = null;
        try { stored = JSON.parse(localStorage.getItem(key()) || 'null'); } catch (_) { /* unreadable */ }
        if (stored) return false;
        savePending(intent); return true;
      });
      if (!reserved) {
        try { D.pending = JSON.parse(localStorage.getItem(key()) || 'null'); } catch (_) { /* ignored */ }
        throw new Error('A DEC change from another tab is still unfinished. Check it before signing again.');
      }
      D.signing = true;
      try {
        const result = await requestBroadcastThroughGate(D.player, fresh.operations, response => {
          const tx = response?.result?.id || response?.result?.tx_id || response?.tx_id;
          if (typeof tx === 'string' && /^[a-f0-9]{40}$/i.test(tx)) savePending({ ...D.pending, tx_id: tx, signed_at: D.pending?.signed_at || Date.now() });
        });
        savePending({ ...D.pending, tx_id: result.txId, signed_at: D.pending?.signed_at || Date.now() });
        D.signing = false;
        await check();
      } catch (error) {
        if (error.broadcasted === false || (error.retryable && !error.ambiguousBroadcast)) savePending(null);
        throw error;
      }
    }
  } catch (error) { if (generation === D.generation) D.error = error.message; }
  finally { if (generation === D.generation) { D.busy = false; D.signing = false; render(); } }
}
// extracted from the trigger's own click listener so the new
// #wallet-edge-tab (index.html) can open the same drawer through the same path
// instead of duplicating this logic or firing a synthetic click.
// `symbol`, when a valid WALLET_TABS entry, opens the drawer directly on
// that resource (the edge tray's own option click) -- set BEFORE the pending-receipt
// pending-claim overrides below, which still win over any explicit selection. A
// caller with no argument (the mobile #regional-dec-button) gets the persisted
// per-viewer selection instead, default 'DEC'.
export async function openWalletDrawer(symbol) {
  if (!account() || D.closing) return;
  if (D.player !== account()) expandedEmptyRegions.clear();
  D.player = account(); D.focus = document.activeElement; D.review = null; D.data = null; D.wallet = null; D.rewards = null; D.message = ''; D.error = '';
  try { D.pending = JSON.parse(localStorage.getItem(key()) || 'null'); } catch (_) { D.pending = null; }
  D.claim = { review: null, pending: loadClaimPending(D.player), busy: false, checking: false };
  D.tab = WALLET_TABS.includes(symbol) ? symbol : getWalletSelection();
  // a pending DEC receipt (possibly written by another tab) must be reachable — open on the DEC tab; a pending claim on SPS.
  if (D.pending) D.tab = 'DEC'; else if (D.claim.pending) D.tab = 'SPS';
  else if (swapLocked(D.player)) D.tab = swapLocked(D.player);  // a swap awaiting its wallet/game answer keeps its tab
  D.busy = true; render(true);
  // the desktop panel is deliberately non-modal ("a disconnected modal")
  // the board stays usable while it is open; mobile keeps the full-height modal drawer.
  if (isDesktopPanel()) {
    const alreadyOpen = drawer.open;
    if (!alreadyOpen) { drawer.dataset.walletMotion = 'closed'; drawer.show(); }
    positionDesktopPanel();
    setWalletSurfaceTarget(true);
  } else drawer.showModal();
  drawer.querySelector('[data-dec="close"]')?.focus({ preventScroll: true });
  setWalletTriggersExpanded(true);
  if (!isDesktopPanel() && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    drawer.querySelector('.dec-drawer-panel').animate([{ transform: 'translateX(100%)' }, { transform: 'translateX(0)' }], { duration: 240, easing: 'cubic-bezier(.4, 0, .2, 1)' });
  }
  await load();
  if (drawer.open && D.pending?.tx_id && (D.pending.status === 'pending' || (D.pending.status === 'confirmed' && !D.pending.harvest))) await act('verify', trigger);
  if (drawer.open && D.claim.pending?.status === 'pending') await act('claim:verify', trigger);
}

export function initRegionalDec() {
  trigger = document.getElementById('regional-dec-button');
  if (!trigger) return;
  trigger.innerHTML = `<img src="${esc(tokenIcon('DEC'))}" alt=""><span>Wallet</span>`;  // the DEC button is now the wallet
  // the edge tab is now #wallet-picker's toggle -- an icon-only tray of
  // WALLET_TABS options (mirroring Iris's own theme picker) that opens the wallet
  // drawer on the selected resource in the same motion.
  walletPicker = document.getElementById('wallet-picker');
  walletToggleBtn = document.getElementById('wallet-edge-tab');
  walletTray = walletPicker?.querySelector('.edge-tray') || null;
  if (walletPicker && walletToggleBtn && walletTray) {
    // (maintainer): "add a slide in chevron to the left" -- a non-resource option
    // prepended to the tray, that only closes it (the same outcome as click-outside).
    const chevronHtml = `<button type="button" class="edge-opt edge-tray-chevron" data-tip="Slide in" aria-label="Slide the tray in"><span class="ceres-chevron" aria-hidden="true"></span></button>`;
    walletTray.innerHTML = chevronHtml + WALLET_TABS.map(symbol =>
      `<button type="button" class="edge-opt" role="tab" data-symbol="${symbol}" aria-selected="false" data-tip="${esc(tokenLabel(symbol))}" aria-label="${esc(tokenLabel(symbol))}"><img src="${esc(tokenIcon(symbol))}" alt=""></button>`).join('');
    updateWalletToggleIcon(getWalletSelection());
    updateWalletTrayActive(getWalletSelection());
    walletToggleBtn.addEventListener('click', () => toggleWalletPicker());
    walletTray.addEventListener('click', (event) => {
      if (event.target.closest('.edge-tray-chevron')) { closeWalletPicker(); walletToggleBtn.focus({ preventScroll: true }); return; }
      const opt = event.target.closest('.edge-opt');
      if (!opt || !opt.dataset.symbol) return;
      setWalletSelection(opt.dataset.symbol);
      walletToggleBtn.focus({ preventScroll: true });
      openWalletDrawer(opt.dataset.symbol);
    });
    document.addEventListener('click', (event) => {
      const insideDrawer = event.composedPath().includes(drawer);
      if (walletPicker.dataset.open === 'true' && !walletPicker.contains(event.target) && !insideDrawer) closeWalletPicker();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && walletPicker.dataset.open === 'true') closeWalletPicker();
    });
  }
  drawer = document.createElement('dialog'); drawer.id = 'regional-dec-drawer'; drawer.className = 'dec-drawer';
  drawer.setAttribute('aria-labelledby', 'dec-title');
  drawer.innerHTML = '<div class="dec-drawer-backdrop" aria-hidden="true"></div><div class="dec-drawer-panel"></div>';
  document.body.append(drawer);
  drawer.addEventListener('cancel', event => { event.preventDefault(); close(); });
  let backdropPointer = false;
  drawer.addEventListener('pointerdown', event => { backdropPointer = event.target.classList?.contains('dec-drawer-backdrop'); });
  drawer.addEventListener('click', event => {
    if (backdropPointer && event.target.classList?.contains('dec-drawer-backdrop')) { backdropPointer = false; close(); return; }
    backdropPointer = false;
    // the receipt's Show costs button is the harvest modal's own (data-hm="show-costs", same costsOpen set)
    // render() keeps the receipt on screen where it was ().
    const costs = event.target.closest('[data-hm="show-costs"]');
    if (costs && !costs.disabled && drawer.contains(costs)) {
      const uid = costs.dataset.region || 'total';
      if (D.costsOpen.has(uid)) D.costsOpen.delete(uid); else D.costsOpen.add(uid);
      render();
      const again = [...drawer.querySelectorAll('[data-hm="show-costs"]')].find((b) => (b.dataset.region || 'total') === uid);
      again?.focus({ preventScroll: true });
      return;
    }
    const target = event.target.closest('[data-dec]');
    if (target && !target.disabled) act(target.dataset.dec, target);
  });
  drawer.addEventListener('toggle', event => {
    if (!event.target.matches('.dec-region-collapsible')) return;
    const uid = event.target.dataset.region;
    if (event.target.open) expandedEmptyRegions.add(uid);
    else expandedEmptyRegions.delete(uid);
  }, true);
  drawer.addEventListener('input', event => {
    if (event.target.matches?.('[data-lp-amount]')) { swapInput(swapContext(), event.target); return; }
    if (event.target.matches?.('[data-lp-receive]')) { swapReceive(swapContext(), event.target); return; }
    if (event.target.matches?.('[data-lp-pool-amount], [data-lp-pool-pct], [data-lp-pool-dec]')) { poolInput(swapContext(), event.target); return; }
    if (event.target.matches?.('[data-lp-transfer-to], [data-lp-transfer-amount]')) { transferInput(swapContext(), event.target); return; }
    if (event.target.id !== 'dec-amount' || !D.review || D.busy) return;
    const position = event.target.selectionStart;
    D.review.amount = event.target.value;
    D.error = '';
    queueStakePreview();
    render();
    const input = drawer.querySelector('#dec-amount');
    input?.focus();
    if (position != null) input?.setSelectionRange(position, position);
  });
  drawer.addEventListener('change', event => {
    if (event.target.matches?.('select[data-lp-region]')) swapRegion(swapContext(), event.target);
    if (event.target.matches?.('select[data-lp-pool-region]')) poolRegion(swapContext(), event.target);
    if (event.target.matches?.('select[data-lp-transfer-from], select[data-lp-transfer-region]')) transferSelect(swapContext(), event.target);
  });
  // the desktop panel is non-modal (drawer.show(), not showModal()), so it
  // gets no native backdrop click-to-close or Escape-to-close -- both are wired here.
  // A click landing on the edge tray itself is left alone (that click's own listener
  // above, already decides whether to select/open/close).
  let desktopPanelPointerDownInside = false;
  document.addEventListener('pointerdown', (event) => {
    if (!drawer.open || !isDesktopPanel()) { desktopPanelPointerDownInside = false; return; }
    const panel = drawer.querySelector('.dec-drawer-panel');
    desktopPanelPointerDownInside = !!(panel?.contains(event.target) || walletPicker?.contains(event.target));
  });
  document.addEventListener('click', (event) => {
    if (!drawer.open || !isDesktopPanel()) return;
    const panel = drawer.querySelector('.dec-drawer-panel');
    const pointerDownInside = desktopPanelPointerDownInside;
    desktopPanelPointerDownInside = false;
    if (!desktopPanelClickShouldClose({
      pointerDownInside,
      targetConnected: event.target?.isConnected !== false,
      panelContains: !!panel?.contains(event.target),
      pickerContains: !!walletPicker?.contains(event.target),
    })) return;
    close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && drawer.open && isDesktopPanel()) close();
  });
  // Team-lead correction (2026-09-19): #wallet-picker's position is not a static
  // constant -- keep the desktop panel tracking it (positionDesktopPanel() itself
  // no-ops unless the panel is actually open on desktop, so these are cheap while closed).
  window.addEventListener('resize', positionDesktopPanel);
  window.addEventListener('scroll', positionDesktopPanel, true);
  window.addEventListener('ceres:table-layout', positionDesktopPanel);
  // Pre-landing review fix: a bare `openWalletDrawer` handler receives the click
  // Event as its first argument -- harmless today (WALLET_TABS.includes(event) is
  // false, so it falls through to the persisted selection) but not the intended call.
  trigger.addEventListener('click', () => openWalletDrawer());
  const barWallet = document.getElementById('mobile-wallet-btn'); // the phone's bottom-bar wallet
  if (barWallet) {
    const art = barWallet.querySelector('.mobile-action-dec');
    if (art) art.src = tokenIcon('DEC');
    barWallet.addEventListener('click', () => openWalletDrawer());
  }
  window.addEventListener('ceres:wallet-changed', () => { if (drawer.open && !D.busy) render(); });
  onBalancesChanged(({ assets }) => {
    if (!drawer.open || !D.player || account() !== D.player) return;
    if (!assets.some(asset => WALLET_TABS.includes(asset))) return;
    loadWallet({ quiet: true });
    if (assets.includes('DEC')) api('').then(data => { if (drawer.open && data.player === D.player) { D.data = data; render(); } }).catch(() => {});
    if (assets.includes('SPS')) loadRewards();
  }, { player: () => D.player });
}
