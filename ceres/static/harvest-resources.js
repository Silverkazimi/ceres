import { esc } from './format.js';
import { fmt2, tokenIcon, tokenLabel } from './harvest-state.js';
import { netAfterCosts } from './harvest-net.js';
import { resourceOrder } from './resources.js';

export const RESOURCE_ORDER = resourceOrder('harvest'); // registry: GRAIN WOOD STONE IRON AURA SPS RESEARCH
export function resourceKeys(values) {
  const rank = key => RESOURCE_ORDER.includes(key) ? RESOURCE_ORDER.indexOf(key) : RESOURCE_ORDER.length;
  return Object.keys(values || {}).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export function resourcePairStartsVisualRow(previousTop, currentTop) {
  return previousTop == null || currentTop > previousTop + 1;
}

export function syncHarvestResourceDividers(root) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('.hm-totals').forEach((totals) => {
    let previousTop = null;
    [...totals.children].filter(pair => pair.classList?.contains('hm-resource-pair')).forEach((pair) => {
      const top = pair.getBoundingClientRect().top;
      pair.classList.toggle('is-row-start', resourcePairStartsVisualRow(previousTop, top));
      previousTop = top;
    });
  });
}

export function observeHarvestResourceDividers(root) {
  if (!root || root.__harvestResourceDividerObserver) return;
  const sync = () => syncHarvestResourceDividers(root);
  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(sync);
    observer.observe(root);
    root.__harvestResourceDividerObserver = observer;
    return;
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', sync);
    root.__harvestResourceDividerObserver = { disconnect: () => window.removeEventListener('resize', sync) };
  }
}

export function harvestResourceColumns(received, costs, { pending = false, liveToken = '', sharedKeys = null, receipt = false, net = false } = {}) {
  // a resource the plot does not produce must not appear at all. The payload
  // can carry GRAIN: 0 for every plot, and listing it made a non-grain plot read
  // "Grain 0" -- and, because it counted as a second resource, it also defeated the
  // single-plot pair layout. A received resource shows only when something was
  // actually received, when it has a cost of its own, or while amounts are pending.
  const participates = key => Number(received?.[key]) !== 0
    || Number(costs?.[key]) !== 0
    || (pending && (Object.hasOwn(received || {}, key) || Object.hasOwn(costs || {}, key)));
  const receivedKeys = resourceKeys(received).filter((key) => {
    if (net) return participates(key);
    return Number(received?.[key]) > 0 || Number(costs?.[key]) > 0
      || (pending && Object.hasOwn(received || {}, key));
  });
  const costKeys = resourceKeys(costs).filter((key) => {
    if (net) return participates(key);
    return Number(costs?.[key]) > 0 || (pending && Object.hasOwn(costs || {}, key));
  });
  const localKeys = resourceKeys(Object.fromEntries(
    [...new Set([...receivedKeys, ...costKeys])].map(key => [key, true]),
  ));
  // alignment may order local tiles, never create one with no local change
  // sharedKeys may only reorder what is already local, never introduce a foreign resource.
  const orderedSharedKeys = (sharedKeys || []).filter(key => localKeys.includes(key));
  const keys = [...orderedSharedKeys, ...localKeys.filter(key => !orderedSharedKeys.includes(key))];
  const number = value => pending ? '<span class="hm-skel is-num" aria-hidden="true"></span>' : fmt2(value);
  return `<div class="hm-totals${receipt ? ' hm-totals--receipt' : ''}${net ? ' hm-totals--net' : ''}">${keys.map(key => {
    const icon = tokenIcon(key);
    const art = icon ? `<img src="${esc(icon)}" alt="">` : '';
    const label = esc(tokenLabel(key));
    const credit = received == null ? null : net ? Number(received[key] || 0) - Number(costs?.[key] || 0) : Number(received[key] || 0);
    const hasCredit = net || credit > 0 || (pending && Object.hasOwn(received || {}, key));
    const creditHook = liveToken === key ? ' data-hl="net"' : '';
    const debitHook = liveToken ? ` data-hl="cost-${esc(key)}"` : '';
    const debit = Number(costs?.[key]);
    const hasDebit = !net && (debit > 0 || (pending && Object.hasOwn(costs || {}, key)));
    const resourceArt = art ? art.replace('<img ', '<img class="hm-resource-art" ') : '';
    const netClass = net ? ` hm-resource-net is-${credit < 0 ? 'loss' : credit > 0 ? 'gain' : 'even'}` : '';
    const creditLabel = net ? `Net ${credit < 0 ? 'loss' : credit > 0 ? 'gain' : 'change'}` : 'Received';
    const debitHtml = hasDebit ? `<div class="hm-total hm-resource-debit" aria-label="${label} cost"><b>−<span${debitHook}>${number(debit)}</span></b><small>Cost</small></div>` : '';
    const creditHtml = hasCredit ? `<div class="hm-total hm-resource-credit${netClass}" aria-label="${label} ${creditLabel.toLowerCase()}"><b${creditHook}>${number(credit)}</b><small>${creditLabel}</small></div>` : '';
    return `<div class="hm-resource-pair${art ? '' : ' is-no-art'}" data-resource="${esc(key)}">${resourceArt}<span class="hm-resource-label">${label}</span><div class="hm-resource-values">${creditHtml}${debitHtml}</div></div>`;
  }).join('')}</div>`;
}

export function singlePlotHarvestLedger(projection, { pending = false } = {}) {
  const rows = netAfterCosts(projection);
  const number = amount => pending || amount == null ? '<span class="hm-skel is-num" aria-label="Amount pending"></span>' : fmt2(Math.abs(amount));
  const ownCost = projection?.costs?.[String(projection?.token || '').toUpperCase()];
  const hasOwnCost = projection?.net != null && Number.isFinite(Number(projection.net))
    && ownCost != null && Number.isFinite(Number(ownCost)) && Number(ownCost) > 0;
  return `<div class="hm-plot-ledger">${rows.map(({ symbol, amount, kind }) => {
    const gain = kind === 'gain';
    const label = esc(tokenLabel(symbol));
    const icon = tokenIcon(symbol);
    const art = icon ? `<img class="hm-resource-art" src="${esc(icon)}" alt="">` : '';
    return `<div class="hm-plot-ledger-row${gain ? ' is-gain' : ''}${gain && amount < 0 ? ' is-loss' : ''}" data-resource="${esc(symbol)}"><span class="hm-total ${gain ? 'hm-resource-credit' : 'hm-resource-debit'}">${art}<span><b>${gain ? `<span data-hl="net">${amount < 0 ? '−' : '+'}${number(amount)}</span>` : `−<span data-hl="cost-${esc(symbol)}">${number(amount)}</span>`}</b><small>${label} ${gain ? (hasOwnCost ? 'received · after tax and costs' : 'received · after tax') : 'cost'}</small></span></span></div>`;
  }).join('')}</div>`;
}
