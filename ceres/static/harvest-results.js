import { RESOURCE_ORDER, resourceKeys, harvestResourceColumns } from './harvest-resources.js';
import { abilityIconHtml, TOTEM_ICON_FILE } from './icons.js';
import { editorCardImageHtml } from './editor.js';
import { ASSET_BASE } from './state.js';
import { esc, formatPlotLabel } from './format.js';
import { netAfterCosts } from './harvest-net.js';
import { batchLedgerRows, batchSlotsHtml, batchRegionPlaqueHtml, batchRegionLedgerHtml, batchTotalHtml } from './harvest-ledger.js';
import { fmt2, tokenIcon, tokenLabel } from './harvest-state.js';

const ORDER = RESOURCE_ORDER;
const FRAGMENT_CODES = { TOTEMFC: 'common', TOTEMFR: 'rare', TOTEMFE: 'epic', TOTEMFL: 'legendary' };
const amounts = (values) => Object.entries(values || {}).filter(([, n]) => n != null && Number.isFinite(Number(n)) && Number(n) > 0)
  .sort(([a], [b]) => (ORDER.indexOf(a) < 0 ? ORDER.length : ORDER.indexOf(a)) - (ORDER.indexOf(b) < 0 ? ORDER.length : ORDER.indexOf(b)));

function resources(values) {
  return `<dl class="harvest-receipt-resources">${amounts(values).map(([key, value]) => {
    const icon = tokenIcon(key);
    return `<div>${icon ? `<img src="${esc(icon)}" alt="" aria-hidden="true">` : ''}<dt>${esc(tokenLabel(key))}</dt><dd>${fmt2(value)}</dd></div>`;
  }).join('')}</dl>`;
}

export function rewardCelebrations(member) {
  const finds = (member.finds || []).filter((find) => find.kind === 'fragment' || find.kind === 'labors_luck');
  if (!finds.length) return '';
  return `<section class="harvest-discovery" aria-label="Bonus rewards found"><header><p>A remarkable harvest</p><h3>${finds.length === 1 ? 'You found a treasure!' : 'You found treasures!'}</h3></header><div class="harvest-discovery-grid">${finds.map((find) => {
    const card = find.kind === 'labors_luck';
    const tier = FRAGMENT_CODES[String(find.type || '').toUpperCase()] || String(find.type || '').toLowerCase(); // raw game codes (TOTEMFC...) from the DEC/receipt paths
    const knownTier = TOTEM_ICON_FILE.has(tier);
    const name = card ? (find.card?.name || 'Land card') : `${knownTier ? tier.charAt(0).toUpperCase() + tier.slice(1) + ' ' : ''}Totem fragment`;
    const art = card ? (find.card ? editorCardImageHtml(find.card, { alt: `${name} — harvested land card`, pickKey: `reward:${find.uid}` }) : '')
      : knownTier ? `<img src="${ASSET_BASE}/totems/totem-${tier}.png" alt="${esc(name)}" loading="lazy">` : '';
    return `<article class="harvest-discovery-prize${card ? ' is-land-card' : ' is-fragment'}"><div class="harvest-discovery-art">${art || `<span class="harvest-discovery-fallback">${card ? abilityIconHtml('LL') : '✦'}<span>Artwork unavailable</span></span>`}</div><div class="harvest-discovery-caption"><p>${card ? `${abilityIconHtml('LL')} Labor’s Luck` : 'Totem discovery'}</p><h4>${esc(name)}</h4><span>${card ? 'A new land card found in your harvest.' : 'A fragment for your Totem collection.'}</span></div></article>`;
  }).join('')}</div></section>`;
}

function ledgerAmounts(received, costs) {
  if (received == null || costs == null) return null;
  const result = Object.create(null);
  for (const symbol of new Set([...Object.keys(received), ...Object.keys(costs)])) {
    const amount = Number(received[symbol] || 0);
    const cost = Number(costs[symbol] || 0);
    if (!Number.isFinite(amount) || !Number.isFinite(cost)) continue;
    result[symbol] = { received: amount, cost, net: netAfterCosts({ token: symbol, net: amount, costs: { [symbol]: cost } })[0].amount };
  }
  return result;
}

function sumLedgers(members) {
  const total = Object.create(null);
  for (const member of members) {
    const ledger = ledgerAmounts(member.received_by_token, member.costs_by_token);
    if (!ledger) return null;
    for (const [symbol, values] of Object.entries(ledger)) {
      const current = total[symbol] || { received: 0, cost: 0 };
      current.received += values.received;
      current.cost += values.cost;
      total[symbol] = current;
    }
  }
  for (const [symbol, values] of Object.entries(total)) values.net = netAfterCosts({ token: symbol, net: values.received, costs: { [symbol]: values.cost } })[0].amount;
  return total;
}

function receiptSlots(ledger, kind, shared = null) {
  const resources = shared || [...RESOURCE_ORDER, ...Object.keys(ledger).filter((symbol) => !RESOURCE_ORDER.includes(symbol)).sort()];
  const visible = Object.keys(ledger);
  const values = Object.fromEntries(visible.map((symbol) => [symbol, ledger[symbol][kind]]));
  return batchSlotsHtml(resources, values, visible, kind);
}

function receiptLedgerHtml(ledger, { total = false, costsOpen = false, regionUid = '', regionNumber = '', resources = null, collectedDot = false, extra = '' } = {}) {
  const received = receiptSlots(ledger, 'received', resources);
  const costs = receiptSlots(ledger, 'cost', resources);
  const net = receiptSlots(ledger, 'net', resources);
  if (total) {
    const toggle = `<button type="button" class="hm-batch-costs ceres-btn ceres-btn--secondary" data-control-density="compact" data-hm="show-costs" aria-expanded="${costsOpen}">${costsOpen ? 'Hide costs' : 'Show costs'}</button>`;
    const lines = costsOpen ? batchLedgerRows(received, costs, net) : `<div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label" aria-hidden="true"></span>${net}<span class="hm-batch-toggle-spacer" aria-hidden="true"></span></div>`;
    return batchTotalHtml('Total · net after tax and costs', '', toggle, lines);
  }
  const hasCosts = Object.values(ledger).some((values) => values.cost > 0);
  const toggle = `<button type="button" class="hm-batch-row-toggle" data-hm="show-costs" data-region="${esc(regionUid)}" aria-expanded="${costsOpen}" aria-label="${costsOpen ? 'Hide' : 'Show'} costs for region ${esc(regionNumber)}"${hasCosts ? '' : ' disabled'}><span class="ceres-chevron" aria-hidden="true"></span></button>`;
  const plaque = batchRegionPlaqueHtml(regionNumber, regionUid);
  const dot = collectedDot ? '<span class="hm-batch-state-dot is-ok" data-tip="Collected" aria-label="Collected"></span>' : '';
  const actions = `<span class="hm-batch-region-actions">${dot}${toggle}</span>`;
  const lines = batchRegionLedgerHtml({ leading: plaque, received, costs, net, actions, open: costsOpen && hasCosts });
  return `<div class="hm-batch-region" data-region="${esc(regionUid)}">${lines}${extra}</div>`;
}

function bonuses(member) {
  const finds = member.finds || [];
  const eligible = member.reward_eligibility || {};
  const rows = [];
  for (const [kind, label] of [['fragment', 'Totem fragments'], ['labors_luck', 'Labor’s Luck']]) {
    const awarded = finds.filter((find) => find.kind === kind);
    if (awarded.length || eligible[kind] !== 'eligible') continue;
    const icon = kind === 'labors_luck' ? abilityIconHtml('LL') : '';
    const detail = (member.evidence?.includes('reward_action') || member.evidence?.includes('regional_rewards')) ? (kind === 'fragment' ? 'No fragment found this harvest.' : 'No card found this harvest.') : 'Reward result unavailable.';
    rows.push(`<li>${icon}<div><strong>${label}</strong><span>${detail}</span></div></li>`);
  }
  if (rows.length) return `<section class="harvest-receipt-bonus"><h4>Bonus rewards</h4><ul class="harvest-receipt-rewards">${rows.join('')}</ul></section>`;
  if (finds.length || (eligible.fragment === 'ineligible' && eligible.labors_luck === 'ineligible')) return '';
  return '';
}

export function harvestSuccessHtml(run, { tax = false, costsOpen = new Set(), layout = null } = {}) {
  const members = run.members || [];
  const confirmed = members.filter((member) => member.outcome === 'success');
  const complete = confirmed.length === members.length;
  const totals = sumLedgers(confirmed);
  const receivedTotals = Object.create(null);
  for (const member of confirmed) for (const [key, value] of amounts(member.received_by_token)) receivedTotals[key] = (receivedTotals[key] || 0) + Number(value);
  const regionLedger = !tax && isRegionRun(members);
  // on desktop the receipt IS the Harvest all ledger (same columns, wrappers and CSS).
  // At <=1279 the receipt stays unchanged until the phone redesign ().
  const desktop = regionLedger && !!totals && Object.keys(totals).length > 0 && document.documentElement.classList?.contains('ceres-lw-le-1279') !== true;
  const shared = desktop ? resourceKeys(Object.fromEntries([...(layout?.resources || []), ...Object.keys(totals)].map((symbol) => [symbol, true]))) : null;
  const title = complete ? (tax ? 'Taxes collected' : 'Harvest complete') : 'Collection finished';
  const subject = `${confirmed.length}${complete ? '' : ` of ${members.length}`} ${tax ? (confirmed.length === 1 ? 'tax claim' : 'tax claims') : (confirmed.length === 1 ? 'collection' : 'collections')}`;
  const total = regionLedger && totals && Object.keys(totals).length
    ? desktop ? receiptLedgerHtml(totals, { total: true, costsOpen: costsOpen.has('total'), resources: shared })
      : `<div class="harvest-receipt-ledger">${receiptLedgerHtml(totals, { total: true, costsOpen: costsOpen.has('total') })}</div>`
    : amounts(receivedTotals).length ? resources(receivedTotals) : '<p class="harvest-receipt-note">Collection confirmed. Resource amounts are not available in this receipt.</p>';
  const cards = members.map((member) => {
    const isRegion = regionLedger && !member.deed_uids?.length;
    const where = isRegion ? '' : `Plot ${member.plotLabel || formatPlotLabel(member.deed_uids[0])}`;
    const regionNumber = String(member.region_uid || '').split('-').pop().padStart(3, '0');
    if (member.outcome !== 'success') {
      const status = member.outcome === 'not_sent' ? 'Not sent' : member.outcome === 'failed' ? 'Failed' : 'Not confirmed';
      const message = member.error || (member.outcome === 'not_sent' ? 'This operation was not sent. No collection result is recorded.' : 'No confirmed receipt is available for this operation.');
      return `<article class="harvest-receipt"><header>${isRegion ? batchRegionPlaqueHtml(regionNumber, member.region_uid) : `<h3>${esc(where)}</h3>`}<span class="harvest-receipt-status is-muted">${status}</span></header><p class="harvest-receipt-note">${esc(message)}</p></article>`;
    }
    const ledger = ledgerAmounts(member.received_by_token, member.costs_by_token);
    // (maintainer: the same data twice): with one confirmed region the Total box already shows exactly this ledger; only its extras stay.
    if (regionLedger && members.length === 1 && isRegion && ledger) {
      const extras = `${rewardCelebrations(member)}${bonuses(member)}`;
      return extras ? `<article class="harvest-receipt">${extras}</article>` : '';
    }
    if (desktop && isRegion && ledger) return receiptLedgerHtml(ledger, { regionUid: member.region_uid, regionNumber, costsOpen: costsOpen.has(member.region_uid), resources: shared, collectedDot: true, extra: `${rewardCelebrations(member)}${bonuses(member)}` });
    const financials = isRegion && ledger
      ? `<div class="harvest-receipt-ledger">${receiptLedgerHtml(ledger, { regionUid: member.region_uid, regionNumber, costsOpen: costsOpen.has(member.region_uid) })}</div>`
      : `<div class="harvest-receipt-financials hm-resource-summary"><h4>${tax ? 'Received' : 'Received · after tax'}</h4>${harvestResourceColumns(member.received_by_token, member.costs_by_token, { receipt: true })}${member.received_by_token == null ? '<p class="harvest-receipt-note">Received amounts unavailable</p>' : ''}${member.costs_by_token == null ? '<p class="harvest-receipt-note">Resource costs unavailable</p>' : ''}</div>`;
    const heading = isRegion && ledger ? '' : isRegion ? batchRegionPlaqueHtml(regionNumber, member.region_uid) : `<h3>${esc(where)}</h3>`;
    return `<article class="harvest-receipt"><header>${heading}<span class="harvest-receipt-status">Collected</span></header>${rewardCelebrations(member)}${financials}${bonuses(member)}</article>`;
  }).join('');
  const receiptsClass = regionLedger ? 'harvest-receipts is-batch-ledger' : 'harvest-receipts';
  const tx = run.txId ? `<a class="harvest-receipt-transaction" href="https://hivehub.dev/tx/${esc(run.txId)}" target="_blank" rel="noopener">View transaction <span>${esc(run.txId.slice(0, 8))}…${esc(run.txId.slice(-6))}</span></a>` : '';
  if (desktop) return `<section class="harvest-receipt-summary${complete ? '' : ' is-partial'}" aria-label="Collection summary"><div class="harvest-receipt-success" role="status"><span aria-hidden="true">${complete ? '✓' : '!'}</span><div><h2>${title}</h2><p>${subject} confirmed by the game.</p></div></div></section><div class="hm-batch-grid">${total}<section class="hm-batch-regions"><h3 class="hm-sec-h">Regions</h3><div class="hm-batch-region-list">${cards}</div></section></div>${tx}`;
  return `<section class="harvest-receipt-summary${complete ? '' : ' is-partial'}" aria-label="Collection summary"><div class="harvest-receipt-success" role="status"><span aria-hidden="true">${complete ? '✓' : '!'}</span><div><h2>${title}</h2><p>${subject} confirmed by the game.</p></div></div>${regionLedger && totals && Object.keys(totals).length ? total : members.length > 1 ? `<h3>${tax ? 'Total received' : 'Total received · after tax'}</h3>${total}` : ''}</section><div class="${receiptsClass}">${cards}</div>${tx}`;
}

function isRegionRun(members) {
  return members.length > 0 && members.every((member) => !member.deed_uids?.length);
}

export function harvestSuccessHeadingHtml(run, { tax = false, player = '' } = {}) {
  const count = (run.members || []).filter((member) => member.outcome === 'success').length;
  return `<h2 id="harvest-modal-title">${tax ? 'Tax receipt' : 'Harvest receipt'}</h2><p>${esc(player)} · ${count} confirmed ${tax ? (count === 1 ? 'claim' : 'claims') : (count === 1 ? 'collection' : 'collections')}</p>`;
}
