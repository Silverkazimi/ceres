// the multi-resource views of the wallet drawer. Pure rendering over
// api/wallet data; the DEC tab keeps regional-dec.js's own staking content. Unknown is drawn as
// "unavailable", never as 0.
import { esc, regionLabel } from './format.js';
import { countdownText } from './countdown.js';
import { tokenIcon, tokenLabel } from './harvest-state.js';
import { territorySlug } from './icons.js';

export const WALLET_TABS = ['DEC', 'SPS', 'VOUCHER', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'];
const territories = { PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' };
const fmt = value => Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
const icon = (symbol, cls = 'wallet-token-icon') => tokenIcon(symbol) ? `<img src="${esc(tokenIcon(symbol))}" alt="" class="${cls}">` : '';
// (maintainer): every wallet balance reads like DEC's -- label and amount on one row, the amount large and right-aligned.
const balanceRow = (label, value, symbol) => `<p class="dec-liquid">${esc(label)} <strong class="dec-staked">${value == null ? '<span class="wallet-unavailable">unavailable</span>' : `${Number(value).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 })}${icon(symbol)}`}</strong></p>`;
const figure = (value, symbol) => value == null ? '<span class="wallet-unavailable">unavailable</span>' : `<span class="wallet-figure">${fmt(value)}${Number(value) !== 0 ? icon(symbol) : ''}</span>`;
export function plaque(uid) {
  const slug = territorySlug(territories[String(uid).split('-')[1]]);
  return `<span class="ch-plot-badge${slug ? ` territory-${slug}` : ''}">${esc(regionLabel(uid))}</span>`;
}

// (a): `allowed`, when given, is the set of tab symbols exempt from `busy` — used so the DEC
// and SPS tabs stay switchable while only a pending DEC receipt or a pending claim blocks them.
// (maintainer): the tab row is now icon-only, mirroring the edge picker's tray look -- no text
// label, a Ceres tooltip (data-tip) carries the resource name instead.
export function walletTabsHtml(active, busy, allowed) {
  return `<nav class="ceres-tabs wallet-tabs wallet-tabs--icons" role="tablist" aria-label="Wallet resource">${WALLET_TABS.map(symbol =>
    `<button type="button" class="wallet-tab wallet-tab--icon" role="tab" aria-selected="${symbol === active}" data-dec="tab:${symbol}" data-tip="${esc(tokenLabel(symbol))}" aria-label="${esc(tokenLabel(symbol))}"${busy && !(allowed && allowed.includes(symbol)) ? ' disabled' : ''}>${icon(symbol)}</button>`).join('')}</nav>`;
}

// (read side): claimable SPS by reward type; the game pays every type in one claim.
function rewardsHtml(wallet) {
  const rewards = wallet.rewards;
  if (!rewards) return `<section class="wallet-card"><h3>Claimable SPS rewards</h3><p class="wallet-unavailable">${wallet.rewardsBusy ? 'Reading rewards…' : 'Reward figures unavailable.'}</p></section>`;
  const rows = rewards.rows.filter(r => Number(r.balance) > 0);
  const meta = rewards.last_claim_date ? `<p class="wallet-meta">Last claimed ${esc(new Date(rewards.last_claim_date).toLocaleString())}.</p>` : '';
  // the game allows one claim per 23 h; until then the card says when, with a live timer, instead of offering the claim.
  const waiting = rewards.next_claim_at && Date.parse(rewards.next_claim_at) > Date.now();
  const wait = waiting ? `<p class="wallet-claim-wait" role="timer"><span class="spinner" aria-hidden="true"></span><span>Next claim in <b data-countdown-until="${esc(rewards.next_claim_at)}" data-countdown-ended="now">${esc(countdownText(rewards.next_claim_at))}</b></span><small>${esc(new Date(rewards.next_claim_at).toLocaleString())}</small></p>` : '';
  const claimable = !waiting && Number(rewards.total) > 0;  // the warning colour means "claim now", never "wait"
  return `<section class="wallet-card${waiting ? ' is-claim-waiting' : claimable ? ' is-claim-ready' : ''}"><h3>${waiting ? 'SPS rewards building up' : 'Claimable SPS rewards'} <small>${esc(fmt(rewards.total))} SPS in total</small></h3>${wait}${rows.length
    ? `<ul class="wallet-regions wallet-rewards">${rows.map(r => `<li><span class="wallet-reward-label">${esc(r.label)}</span><span class="wallet-region-plots">${r.source === 'land' ? 'land' : 'battle'}</span>${figure(r.balance, 'SPS')}</li>`).join('')}</ul>`
    : '<p>Nothing to claim right now.</p>'}${meta}</section>`;
}

// regions holding something stay listed; zero-balance regions collapse behind one chevron row, closed by
// default. An opened fold stays open across wallet re-renders for this page only (page memory, ).
const OPEN_ZERO_FOLDS = new Set();
if (typeof document !== 'undefined') {
  document.addEventListener('toggle', (event) => {
    const fold = event.target;
    if (!fold?.matches?.('details.wallet-zero-regions')) return;
    if (fold.open) OPEN_ZERO_FOLDS.add(fold.dataset.symbol); else OPEN_ZERO_FOLDS.delete(fold.dataset.symbol);
  }, true);
}
const regionRow = (r, symbol) => `<li${Number(r.balances[symbol]) === 0 ? ' class="is-zero"' : ''}>${plaque(r.region_uid)}<span class="wallet-region-plots">${r.plots_owned != null ? `${r.plots_owned} plots` : ''}</span>${figure(r.balances[symbol], symbol)}</li>`;
function regionsHtml(symbol, wallet) {
  if (!wallet.regions) return `<section class="wallet-card"><h3>By region</h3><p class="wallet-unavailable">Regional balances unavailable.</p></section>`;
  // every region with land or a balance, highest holding first.
  const rows = wallet.regions.filter(r => r.balances[symbol] != null && (Number(r.balances[symbol]) !== 0 || r.plots_owned))
    .sort((a, b) => Number(b.balances[symbol]) - Number(a.balances[symbol]) || String(a.region_uid).localeCompare(String(b.region_uid), undefined, { numeric: true }));
  const held = rows.filter(r => Number(r.balances[symbol]) !== 0);
  const zero = rows.filter(r => Number(r.balances[symbol]) === 0);
  const fold = zero.length ? `<details class="wallet-zero-regions" data-symbol="${esc(symbol)}"${OPEN_ZERO_FOLDS.has(symbol) ? ' open' : ''}><summary>${zero.length} ${zero.length === 1 ? 'region' : 'regions'} with 0</summary><ul class="wallet-regions">${zero.map(r => regionRow(r, symbol)).join('')}</ul></details>` : '';
  return `<section class="wallet-card"><h3>By region</h3>${rows.length ? `${held.length ? `<ul class="wallet-regions">${held.map(r => regionRow(r, symbol)).join('')}</ul>` : '<p>No balance in any region.</p>'}${fold}` : '<p>No balance in any region.</p>'}</section>`;
}

export function walletResourceHtml(symbol, wallet, busy) {
  if (!wallet) return busy ? '' : '<p class="wallet-unavailable">Wallet data unavailable. Refresh to retry.</p>';
  const soulbound = wallet.soulbound?.includes(symbol);
  const land = wallet.resources?.includes(symbol);
  let head = '';
  if (symbol === 'SPS') {
    head = `<section class="wallet-card"><h3>SPS</h3>${balanceRow('Liquid SPS', wallet.account?.SPS, 'SPS')}${balanceRow('Staked SPS', wallet.account?.SPSP, 'SPS')}<p class="wallet-meta">SPS staking rewards are claimed in the game.</p></section>${rewardsHtml(wallet)}`;
  } else if (symbol === 'VOUCHER') {
    head = `<section class="wallet-card"><h3>Vouchers</h3>${balanceRow('Available Vouchers', wallet.account?.VOUCHER, 'VOUCHER')}</section>`;
  } else if (land) {
    const total = wallet.regions ? wallet.regions.reduce((sum, r) => sum + Number(r.balances[symbol] || 0), 0) : null;
    head = `<section class="wallet-card"><h3>${esc(tokenLabel(symbol))}</h3>${balanceRow(`All regions`, total, symbol)}${soulbound ? '<p class="wallet-meta">Soulbound: stays in the region where it was produced; it cannot be moved, sold or pooled.</p>' : ''}</section>`;
  }
  // the LP position and pool-wide reward cards are removed ("unusable and 100% noise"); the swap
  // card (wallet-lp.js) follows the regions card on Grain/Wood/Stone/Iron only, appended by regional-dec.js.
  return head + (land ? regionsHtml(symbol, wallet) : '');
}
