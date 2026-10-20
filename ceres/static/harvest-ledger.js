import { territorySlug } from './icons.js';
import { esc } from './format.js';
import { fmt2, tokenIcon, tokenLabel } from './harvest-state.js';

export function batchBadgeHtml(symbol, amount, kind) {
  const icon = tokenIcon(symbol);
  const art = icon ? `<img src="${esc(icon)}" alt="">` : '<span class="hm-batch-icon-fallback" aria-hidden="true"></span>';
  const number = amount == null ? '<span class="spinner spinner-sm" aria-hidden="true"></span>'
    : `${kind === 'cost' || amount < 0 ? '−' : '+'}${fmt2(Math.abs(amount))}`;
  const name = tokenLabel(symbol);
  return `<span class="hm-batch-badge is-${kind}${kind === 'net' && amount < 0 ? ' is-loss' : ''}" data-resource="${esc(symbol)}"><span class="hm-batch-icon">${art}</span><span class="hm-batch-name" title="${esc(name)}">${esc(name)}</span><span class="hm-batch-number">${number}</span></span>`;
}

export function batchLedgerRows(received, costs, net) {
  const spacer = '<span class="hm-batch-toggle-spacer" aria-hidden="true"></span>';
  return `<div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label">received</span>${received}${spacer}</div><div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label">− costs</span>${costs}${spacer}</div><div class="hm-batch-ledger-line is-net"><span class="hm-batch-ledger-label">= net</span>${net}${spacer}</div>`;
}

export function batchSlotsHtml(resources, values, visibleResources = resources, kind = 'net', loading = false) {
  return `<div class="hm-batch-viewport"><div class="hm-batch-slots">${resources.map((symbol) => {
    if (!visibleResources.includes(symbol)) return `<span class="hm-batch-badge is-empty" data-resource="${esc(symbol)}" aria-hidden="true"></span>`;
    if (loading) return batchBadgeHtml(symbol, null, 'skeleton');
    const value = values[symbol];
    return value == null || value === 0 ? `<span class="hm-batch-badge is-empty" data-resource="${esc(symbol)}" aria-hidden="true"></span>` : batchBadgeHtml(symbol, value, kind);
  }).join('')}</div></div>`;
}

export function batchRegionPlaqueHtml(number, regionUid) {
  const slug = territorySlug({ PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' }[String(regionUid).split('-')[1]]);
  return `<span class="ch-plot-badge hm-batch-plaque${slug ? ` territory-${slug}` : ''}">${esc(number)}</span>`;
}

export function batchRegionLedgerHtml({ leading, expandedLeading = leading, received, costs, net, actions, open }) {
  const line = (label, content, control = '') => `<div class="hm-batch-region-line">${label}${content}${control || '<span class="hm-batch-toggle-spacer" aria-hidden="true"></span>'}</div>`;
  if (!open) return line(leading, net, actions);
  return line(expandedLeading, received, actions)
    + line('<span aria-hidden="true"></span>', costs)
    + line('<span aria-hidden="true"></span>', net);
}

export function batchTotalHtml(title, status, toggle, lines) {
  return `<section class="hm-batch-total" aria-label="Total net after tax and costs"><div class="hm-batch-total-head"><h3>${title}</h3><span class="hm-batch-head-status" role="status" aria-live="polite">${status}</span>${toggle}</div><div class="hm-batch-total-ledger">${lines}</div></section>`;
}
