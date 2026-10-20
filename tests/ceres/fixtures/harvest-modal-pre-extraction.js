// Frozen snapshot of the Harvest all renderer from ceres/static/harvest-modal.js as it was
// before its ledger builders were extracted into harvest-ledger.js. Test data only, never
// loaded by the app: test_harvest_receipt.py renders it next to the current batchBodyHtml()
// and requires byte-identical markup. Two slices: the old helpers, then the old renderer.

function batchBadgeHtml(symbol, amount, kind) {
  const icon = tokenIcon(symbol);
  const art = icon ? `<img src="${esc(icon)}" alt="">` : '<span class="hm-batch-icon-fallback" aria-hidden="true"></span>';
  const number = amount == null ? '<span class="spinner spinner-sm" aria-hidden="true"></span>'
    : `${kind === 'cost' || amount < 0 ? '−' : '+'}${fmt2(Math.abs(amount))}`;
  const name = tokenLabel(symbol);
  const badgeClass = `is-${kind}${kind === 'net' && amount < 0 ? ' is-loss' : ''}`;
  return `<span class="hm-batch-badge ${badgeClass}" data-resource="${esc(symbol)}"><span class="hm-batch-icon">${art}</span><span class="hm-batch-name" title="${esc(name)}">${esc(name)}</span><span class="hm-batch-number">${number}</span></span>`;
}

function batchLedgerRows(received, costs, net) {
  const spacer = '<span class="hm-batch-toggle-spacer" aria-hidden="true"></span>';
  return `<div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label">received</span>${received}${spacer}</div><div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label">− costs</span>${costs}${spacer}</div><div class="hm-batch-ledger-line is-net"><span class="hm-batch-ledger-label">= net</span>${net}${spacer}</div>`;
}

function batchBodyHtml() {
  const layout = M.regionLayout || batchRegionLayout();
  const previewRows = new Map((M.preview?.regions || []).map((region) => [region.region_uid, region]));
  const rows = layout.rows.map((known) => ({ ...known, preview: previewRows.get(known.region_uid) }));
  const chosen = M.scopeKind === 'regions' ? rows.filter((row) => M.selectedRegions.has(row.region_uid)) : rows;
  const fresh = !!M.preview;
  const blocked = chosen.filter((row) => ['short', 'unknown', 'unavailable'].includes(row.preview?.status));
  const blockerLabels = { short: 'Needs funding', unknown: 'Balance unavailable', unavailable: 'Quote unavailable' };
  const blockerCounts = blocked.reduce((counts, row) => {
    counts[row.preview.status] = (counts[row.preview.status] || 0) + 1;
    return counts;
  }, {});
  const active = chosen.filter((row) => Object.keys(row.preview?.net_by_token || {}).length);
  const status = !fresh ? (M.error || 'Reading fresh amounts, costs and balances from the game…')
    : blocked.length ? `${Object.entries(blockerCounts).map(([kind, count]) => `${count} ${blockerLabels[kind]}`).join(' · ')}. ${M.scopeKind === 'all' ? 'Harvest all is blocked.' : 'Blocked.'}`
      : active.length ? '' : M.scopeKind === 'regions' && !chosen.length ? 'Tick one or more regions to harvest.' : 'Nothing to harvest right now.';
  const totals = {};
  chosen.forEach((row) => layout.resources.forEach((symbol) => {
    const amount = totals[symbol] || { received: 0, cost: 0 };
    amount.received += Number(row.preview?.net_by_token?.[symbol] || 0);
    amount.cost += Number(row.preview?.cost_now?.[symbol] || 0);
    totals[symbol] = amount;
  }));
  const slots = (values, kind, resources, loading = false) => `<div class="hm-batch-viewport"><div class="hm-batch-slots">${layout.resources.map((symbol) => {
    if (!resources.includes(symbol)) return `<span class="hm-batch-badge is-empty" data-resource="${esc(symbol)}" aria-hidden="true"></span>`;
    if (loading) return batchBadgeHtml(symbol, null, 'skeleton');
    const value = values[symbol];
    return value == null || value === 0 ? `<span class="hm-batch-badge is-empty" data-resource="${esc(symbol)}" aria-hidden="true"></span>` : batchBadgeHtml(symbol, value, kind);
  }).join('')}</div></div>`;
  const totalValues = (kind) => Object.fromEntries(layout.resources.map((symbol) => {
    const amount = totals[symbol] || { received: 0, cost: 0 };
    const net = netAfterCosts({ token: symbol, net: amount.received, costs: { [symbol]: amount.cost } })[0].amount;
    return [symbol, kind === 'received' ? amount.received : kind === 'cost' ? amount.cost : net];
  }));
  const totalOpen = M.costsOpen.has('total');
  const totalToggle = `<button type="button" class="hm-batch-costs ceres-btn ceres-btn--secondary" data-control-density="compact" data-hm="show-costs" aria-expanded="${totalOpen}"${fresh ? '' : ' disabled aria-hidden="true" tabindex="-1"'}>${totalOpen ? 'Hide costs' : 'Show costs'}</button>`;
  const idleRows = chosen.filter((row) => !row.resources.length);
  const noProduction = idleRows.length ? `<div class="hm-batch-no-production"><span>No production:</span>${idleRows.map((row) => {
    const slug = territorySlug({ PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' }[String(row.region_uid).split('-')[1]]);
    return `<span class="ch-plot-badge hm-batch-plaque${slug ? ` territory-${slug}` : ''}">${esc(row.region_number)}</span>`;
  }).join('')}</div>` : '';
  const totalLines = totalOpen && fresh
    ? batchLedgerRows(slots(totalValues('received'), 'received', layout.resources), slots(totalValues('cost'), 'cost', layout.resources), slots(totalValues('net'), 'net', layout.resources))
    : `<div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label" aria-hidden="true"></span>${slots(totalValues('net'), 'net', layout.resources, !fresh)}<span class="hm-batch-toggle-spacer" aria-hidden="true"></span></div>`;
  const regionLines = rows.map((row) => {
    const number = esc(row.region_number);
    const slug = territorySlug({ PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' }[String(row.region_uid).split('-')[1]]);
    const open = M.costsOpen.has(row.region_uid);
    const plaque = `<span class="ch-plot-badge hm-batch-plaque${slug ? ` territory-${slug}` : ''}">${number}</span>`;
    const picking = M.scopeKind === 'regions';
    const pick = picking ? `<input type="checkbox" class="hm-rcheck" value="${esc(row.region_uid)}" data-hm-region-check aria-label="Harvest region ${number}"${M.selectedRegions.has(row.region_uid) ? ' checked' : ''}${row.resources.length ? '' : ' disabled'}>` : '';
    if (!row.resources.length) return '';
    const idle = fresh && row.preview?.status === 'nothing';
    const received = {}, costs = {}, net = {};
    row.resources.forEach((symbol) => {
      received[symbol] = Number(row.preview?.net_by_token?.[symbol] || 0);
      costs[symbol] = Number(row.preview?.cost_now?.[symbol] || 0);
      net[symbol] = netAfterCosts({ token: symbol, net: received[symbol], costs: { [symbol]: costs[symbol] } })[0].amount;
    });
    const hasCosts = Object.values(costs).some((cost) => cost > 0);
    const toggle = `<button type="button" class="hm-batch-row-toggle" data-hm="show-costs" data-region="${esc(row.region_uid)}" aria-expanded="${open}" aria-label="${open ? 'Hide' : 'Show'} costs for region ${number}"${fresh && hasCosts ? '' : ' disabled'}><span class="ceres-chevron" aria-hidden="true"></span></button>`;
    const state = fresh ? batchStatusPill(row.preview || { status: 'nothing', idle: true }) : { label: 'Preview pending', tone: 'is-muted' };
    const stateColumn = `<span class="hm-batch-region-actions">${state.blocking && !idle ? `<span class="hm-batch-state-label ${state.tone}">${esc(state.label)}</span>` : ''}<span class="hm-batch-state-dot ${state.tone}" data-tip="${esc(state.label)}" aria-label="${esc(state.label)}"></span>${toggle}</span>`;
    const rowSlots = (kind, values) => slots(values, kind, row.resources, !fresh);
    const line = (label, content, control = '') => `<div class="hm-batch-region-line">${label}${content}${control || '<span class="hm-batch-toggle-spacer" aria-hidden="true"></span>'}</div>`;
    const lines = idle
      ? line(picking ? `<span class="hm-batch-leading">${pick}${plaque}</span>` : plaque, `<span class="hm-batch-idle-status hm-batch-state-label ${state.tone}">${esc(state.label)}</span>`, stateColumn)
      : open && fresh
      ? line(picking ? `<span class="hm-batch-leading">${pick}${plaque}</span>` : plaque, rowSlots('received', received), stateColumn)
        + line('<span aria-hidden="true"></span>', rowSlots('cost', costs))
        + line('<span aria-hidden="true"></span>', rowSlots('net', net))
      : line(picking ? `<span class="hm-batch-leading">${pick}${plaque}</span>` : plaque, rowSlots('net', net), stateColumn);
    return `<div class="hm-batch-region${picking ? ' is-picking' : ''}" data-region="${esc(row.region_uid)}">${lines}</div>`;
  }).join('');
  return `${routingEntryHtml()}<div class="hm-batch-grid"><section class="hm-batch-total" aria-label="Total net after tax and costs"><div class="hm-batch-total-head"><h3>Total · net after tax and costs</h3><span class="hm-batch-head-status" role="status" aria-live="polite">${esc(status)}</span>${totalToggle}</div><div class="hm-batch-total-ledger">${totalLines}</div></section><section class="hm-batch-regions"><h3 class="hm-sec-h">Regions</h3><div class="hm-batch-region-list">${regionLines}</div>${noProduction}</section></div>${(M.preview?.issues || []).length ? `<p class="hm-hint">${esc(M.preview.issues.join(' · '))}</p>` : ''}`;
}
