import { esc } from './format.js';
import { tokenIcon, tokenLabel, fmt2 } from './harvest-state.js';
import { territorySlug } from './icons.js';
import { outcomeBoxHtml, poolBoxHtml, routeListColumnHtml, routeNameHtml, routeScrollCueHtml } from './routing-route-list.js';
import { createRoutingRun } from './routing-run.js';
import { plannedBundles } from './routing-layout.js';
import { withWalletChooser } from './routing-window.js';
import { hasSession, isSynced, syncRouting } from './account-space.js';

const TERRITORIES = { PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' };
const SYMBOLS = ['GRAIN', 'WOOD', 'STONE', 'IRON'];

export async function loadEnabledRoutingKinds(player) {
  const response = await fetch(`/api/harvest/routing/v2/kinds?player=${encodeURIComponent(player)}`);
  const value = await response.json();
  if (!response.ok || !Array.isArray(value.enabled_kinds)) throw new Error(value.detail || 'Routing switches unavailable.');
  return { enabled_kinds: value.enabled_kinds, offer_only_kinds: Array.isArray(value.offer_only_kinds) ? value.offer_only_kinds : [], signing_enabled: value.signing_enabled === true };
}

function plaque(region) {
  const parts = String(region || '').split('-');
  const slug = territorySlug(TERRITORIES[parts[1]]);
  const code = parts.pop().padStart(3, '0');
  return `<span class="ch-plot-badge${slug ? ` territory-${slug}` : ''}" aria-label="${esc(regionTitle(region))}">${esc(code)}</span>`;
}

function regionTitle(uid) {
  const parts = String(uid || '').split('-');
  const code = parts.at(-1)?.padStart(3, '0') || String(uid || '');
  const territory = TERRITORIES[parts[1]];
  return `Region ${code}${territory ? ` · ${territory}` : ''}`;
}

function isPercent(rule) {
  return rule.quantity_mode === 'percent';
}

function validPercent(rule) {
  return isPercent(rule) && /^\d+$/.test(String(rule.quantity))
    && Number(rule.quantity) >= 1 && Number(rule.quantity) <= 100;
}

function estimateFor(rule, steps) {
  return steps?.find(step => (step.rule_id || step.id) === rule.id && step.quantity)?.quantity || null;
}

function option(value, selected, label = value, disabled = false) {
  return `<option value="${esc(value)}"${value === selected ? ' selected' : ''}${disabled && value !== selected ? ' disabled' : ''}>${esc(label)}</option>`;
}

function formatQuantity(value) {
  const raw = String(value ?? '').replace(/,/g, '');
  if (!/^\d+(?:\.\d*)?$/.test(raw)) return raw;
  const [whole, fraction = ''] = raw.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}

function formatLegacyQuantity(value) {
  const raw = String(value ?? '').replace(/,/g, '').replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  if (!/^\d+(?:\.\d+)?$/.test(raw)) return raw;
  const [whole, fraction] = raw.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction ? `.${fraction}` : ''}`;
}

// active regions first, each group in region-number order.
function activeFirst(regions) {
  return [...regions].sort((a, b) => Number(b.active !== false) - Number(a.active !== false));
}

// one
// route per region + resource + route kind. Add route picks the first free combination; an edit that would duplicate
// another route is not applied, and choices that would duplicate are shown disabled.
const HARVEST_ROUTE_RESOURCES = ['GRAIN', 'WOOD', 'STONE', 'IRON'];
function routeKindOf(rule) { return rule.kind === 'liquidity' ? (rule.dec_source === 'wallet' ? 'lp_wallet' : 'lp_sell_half') : 'sale_dec'; }
function routeKey(rule) { return `${rule.source_region}|${String(rule.symbol || '').toUpperCase()}|${routeKindOf(rule)}`; }
function takenBy(rules, index) { return new Set(rules.filter((_, at) => at !== index).map(routeKey)); }
// ( and "it is unblocked when switching the
// route kind but this is unclear and hidden"): a choice that would duplicate another
// route keeps the choice and moves the route's other fields to the first free combination; an option is disabled only
// when no free combination exists for it.
const KIND_LABEL = { sale_dec: 'Sell harvested', lp_wallet: 'Add to LP with wallet DEC', lp_sell_half: 'Sell half, then add to LP' };
const KIND_FIELDS = { sale_dec: { kind: 'sell', dec_source: undefined, destination_symbol: 'DEC' },
  lp_wallet: { kind: 'liquidity', dec_source: 'wallet', destination_symbol: undefined },
  lp_sell_half: { kind: 'liquidity', dec_source: 'swap_half', destination_symbol: undefined } };
function freeRoute(candidate, field, taken, kinds) {
  if (!taken.has(routeKey(candidate))) return candidate;
  const kindOrder = field === 'route_kind' ? [routeKindOf(candidate)] : [routeKindOf(candidate), ...kinds.filter(kind => kind !== routeKindOf(candidate))];
  const symbols = field === 'symbol' ? [candidate.symbol] : [candidate.symbol, ...HARVEST_ROUTE_RESOURCES.filter(symbol => symbol !== candidate.symbol)];
  for (const symbol of symbols) for (const kind of kindOrder) {
    const next = { ...candidate, symbol, ...KIND_FIELDS[kind] };
    if (!taken.has(routeKey(next))) return next;
  }
  return null;
}

function ruleHtml(rule, regions, index, enabledKinds, estimate, hasReceipts, taken = new Set(), moved = '', short = null, offerOnlyKinds = []) {
  const blocked = (field, patch) => !freeRoute({ ...rule, ...patch }, field, taken, enabledKinds);
  const lp = rule.kind === 'liquidity';
  const legacy = !isPercent(rule);
  const invalidPercent = isPercent(rule) && !validPercent(rule);
  const selectedKind = lp ? rule.dec_source === 'wallet' ? 'lp_wallet' : 'lp_sell_half' : 'sale_dec';
  const choices = `${enabledKinds.includes('sale_dec') ? option('sale_dec', selectedKind, 'Sell harvested', blocked('route_kind', KIND_FIELDS.sale_dec)) : ''}${enabledKinds.includes('lp_wallet') ? option('lp_wallet', selectedKind, 'Add to LP with wallet DEC', blocked('route_kind', KIND_FIELDS.lp_wallet)) : ''}${enabledKinds.includes('lp_sell_half') && (!offerOnlyKinds.includes('lp_sell_half') || selectedKind === 'lp_sell_half' || short) ? option('lp_sell_half', selectedKind, 'Sell half, then add to LP', blocked('route_kind', KIND_FIELDS.lp_sell_half)) : ''}`;
  const note = legacy ? `${esc(formatLegacyQuantity(rule.quantity))} ${esc(tokenLabel(rule.symbol))} — fixed amount (old style). Change to a percent`
    : invalidPercent ? `Saved ${esc(rule.quantity)} — enter a whole number from 1% to 100%`
    : estimate ? `≈ ${esc(fmt2(estimate))} ${esc(tokenLabel(rule.symbol))}`
    : hasReceipts ? 'Review to calculate from confirmed harvest' : 'Confirm harvest to calculate the amount';
  return `<div class="r3-edit${lp ? ' r3-edit--lp' : ''}" data-r3-rule="${index}"><label class="board-filter-field"><span>Source region</span><select data-r3-field="source_region" data-r3-index="${index}">${activeFirst(regions).map(region => option(region.region_uid, rule.source_region, regionTitle(region.region_uid).split(' · ')[1] || regionTitle(region.region_uid), blocked('source_region', { source_region: region.region_uid }))).join('')}</select></label><label class="board-filter-field"><span>Resource</span><select data-r3-field="symbol" data-r3-index="${index}">${SYMBOLS.map(symbol => option(symbol, rule.symbol, tokenLabel(symbol), blocked('symbol', { symbol }))).join('')}</select></label><label class="r3-share-label"><span>Route percent</span><span class="r3-percent-field"><input type="text" inputmode="numeric" pattern="[0-9]*" placeholder="1–100" value="${esc(isPercent(rule) ? formatLegacyQuantity(rule.quantity) : '')}" data-r3-field="quantity" data-r3-index="${index}" aria-label="Route percent" class="ceres-wide-input" data-control-density="comfortable"><span aria-hidden="true">%</span></span></label><label class="board-filter-field"><span>Route kind</span><select data-r3-field="route_kind" data-r3-index="${index}">${choices}</select></label>${rule.kind === 'liquidity' && rule.dec_source === 'wallet' ? '' : `<label><span>Slippage</span><span class="r3-percent-field"><input type="text" inputmode="decimal" value="${esc(rule.slippage || '2.5')}" data-r3-field="slippage" data-r3-index="${index}" aria-label="Slippage percent" class="ceres-wide-input" data-control-density="comfortable"><span aria-hidden="true">%</span></span></label>`}<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-r3="remove" data-r3-index="${index}" aria-label="Remove route ${esc(tokenLabel(rule.symbol))} to ${lp ? 'LP' : 'DEC'}">Remove</button><small class="r3-allocation-note">${note}</small>${moved ? `<small class="r3-moved-note" role="status">${esc(moved)}</small>` : ''}${short ? `<div class="r3-dec-short" role="alert"><strong>Not enough wallet DEC for this route</strong><span>It needs about ${esc(fmt2(short.needed))} DEC; your wallet has ${esc(fmt2(short.available))} DEC to spare after the protected amount. Selling half of the harvest first pays for it.</span><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-r3="use-sell-half" data-r3-index="${index}">Sell half first instead</button></div>` : ''}</div>`;
}

// the list shows routes by region number, then resource (the
// harvest order Grain, Wood, Stone, Iron…), then action (sale before LP); each chip keeps its own route index.
const ROUTE_RESOURCE_ORDER = ['GRAIN', 'WOOD', 'STONE', 'IRON', 'AURA', 'SPS', 'RESEARCH'];
function displayOrder(rules) {
  const regionNo = (rule) => Number(String(rule.source_region || '').split('-').pop()) || 0;
  const resource = (rule) => { const at = ROUTE_RESOURCE_ORDER.indexOf(String(rule.symbol || '').toUpperCase()); return at < 0 ? ROUTE_RESOURCE_ORDER.length : at; };
  const action = (rule) => rule.kind === 'liquidity' ? (rule.dec_source === 'wallet' ? 1 : 2) : 0;
  return rules.map((_, index) => index).sort((a, b) => regionNo(rules[a]) - regionNo(rules[b])
    || resource(rules[a]) - resource(rules[b]) || action(rules[a]) - action(rules[b]) || a - b);
}

function ruleChip(rule, index, selected, estimate) {
  const icon = tokenIcon(rule.symbol);
  const lp = rule.kind === 'liquidity';
  const allocation = validPercent(rule) ? `${rule.quantity}%${estimate ? ` · ≈ ${fmt2(estimate)} ${tokenLabel(rule.symbol)}` : ''}`
    : isPercent(rule) ? `Saved ${rule.quantity} — enter a whole percent` : `${formatLegacyQuantity(rule.quantity)} ${tokenLabel(rule.symbol)} — fixed amount (old style). Change to a percent`;
  return `<button type="button" class="r3-route-chip${selected ? ' is-selected' : ''}" data-r3="select" data-r3-index="${index}" data-tip="${esc(`${regionTitle(rule.source_region)}: ${allocation} to ${lp ? (rule.dec_source === 'wallet' ? 'LP with wallet DEC' : 'LP after sale') : 'DEC'}. Saved draft.`)}" aria-pressed="${selected}">${plaque(rule.source_region)}${routeNameHtml({ resourceIconHtml: icon ? `<img class="r3-art" src="${esc(icon)}" alt="">` : '', from: tokenLabel(rule.symbol), destination: lp ? 'LP' : 'DEC',
    destinationIconHtml: !lp && tokenIcon('DEC') ? `<img class="r3-art" src="${esc(tokenIcon('DEC'))}" alt="">` : '',
    stateHtml: `<span class="r3-state${validPercent(rule) ? '' : ' is-warn'}">· ${validPercent(rule) ? `${esc(rule.quantity)}%` : 'Fix %'}</span>` })}</button>`;
}

function plannedShares(rules, selectedIndex) {
  const selected = rules[selectedIndex];
  if (!selected) return [];
  if (selected.kind === 'liquidity') return [selected];
  return rules.filter(rule => rule.kind === 'sell' && rule.source_region === selected.source_region
    && rule.symbol === selected.symbol && String(rule.slippage || '2.5') === String(selected.slippage || '2.5'));
}

function plannedFlow(shares, steps) {
  const rule = shares[0];
  const icon = tokenIcon(rule.symbol);
  const target = rule.kind === 'liquidity' ? 'LP shares' : 'DEC';
  const step = steps?.find(item => item.rule_id === rule.id || item.id === rule.id);
  const needed = rule.kind === 'liquidity'
    ? `<span class="r3-dec-needed">${tokenIcon('DEC') ? `<img class="r3-art" src="${esc(tokenIcon('DEC'))}" alt="">` : ''}${step?.dec_funding?.required ? `${fmt2(step.dec_funding.required)} DEC needed at review` : 'DEC needed at review'}</span>` : '';
  const shareLabel = item => validPercent(item) ? `${item.quantity}%${estimateFor(item, steps) ? ` · ≈ ${fmt2(estimateFor(item, steps))}` : ''}`
    : isPercent(item) ? 'Set a whole-number percent' : formatQuantity(item.quantity);
  const estimates = shares.map(item => estimateFor(item, steps));
  const aggregate = shares.length === 1 ? shareLabel(rule)
    : estimates.every(Boolean) ? fmt2(estimates.reduce((sum, value) => sum + Number(value), 0))
    : shares.every(item => !isPercent(item)) ? fmt2(shares.reduce((sum, item) => sum + Number(item.quantity || 0), 0))
    : shares.every(validPercent) ? `${shares.reduce((sum, item) => sum + Number(item.quantity), 0)}%`
    : 'Multiple shares';
  const source = `<div class="r3-flow-source">${plaque(rule.source_region)}${icon ? `<img class="r3-art r3-art--large" src="${esc(icon)}" alt="">` : ''}<b>${esc(tokenLabel(rule.symbol))}</b><span>${esc(aggregate)}</span></div>`;
  const members = shares.length > 1 ? `<div class="r3-members" aria-label="Planned route shares">${shares.map(item => `<div class="r3-member" data-r3-member="${esc(item.id)}">${plaque(item.source_region)}${icon ? `<img class="r3-art" src="${esc(icon)}" alt="">` : ''}<span>${esc(shareLabel(item))} ${esc(tokenLabel(item.symbol))}</span></div>`).join('')}</div>` : '';
  return `<div class="r3-operation-flows"><div class="r3-flowline" data-r3-planned-operation="${esc(rule.id)}"><div class="r3-flow-sources">${source}</div><span class="r3-arrow" aria-hidden="true">⟶</span>${poolBoxHtml({ title: rule.kind === 'liquidity' ? 'Add liquidity' : 'Sell harvested', neededHtml: needed })}<span class="r3-arrow" aria-hidden="true">⟶</span>${outcomeBoxHtml({ iconHtml: tokenIcon(rule.kind === 'liquidity' ? rule.symbol : 'DEC') ? `<img class="r3-art r3-art--large" src="${esc(tokenIcon(rule.kind === 'liquidity' ? rule.symbol : 'DEC'))}" alt="">` : '', label: 'Receives', valueHtml: esc(target) })}</div></div>${members}`;
}

export function createRoutingSetup({ player, scope, regions, receipts = [], produced = null, enabledKinds = [], offerOnlyKinds = [], signingEnabled = false, initialRules = [], onRulesChange = null,
  onRender, isCurrent } = {}) {
  const key = `ceres:harvest-routing:v1:${player}`;
  const allowed = enabledKinds.some(kind => ['sale_dec', 'lp_wallet', 'lp_sell_half'].includes(kind));
  const selected = scope.kind === 'regions' ? regions.filter(region => scope.region_uids.includes(region.region_uid)) : regions;
  // the player's saved routes are their ROOT setup. This screen never deletes, trims or overwrites it: a route that does not fit THIS harvest
  // (other region, nothing produced, kind switched off) or is damaged is only left out of this screen; everything else in storage (other routes, unknown
  // fields, entries it cannot read) is kept as it is. No size cap applies to what is stored.
  const readRoot = () => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return { ok: true, data: {} };
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? { ok: true, data: parsed } : { ok: false, data: {} };
    } catch (_) { return { ok: false, data: {} }; }
  };
  const first = readRoot();
  const saved = first.data;
  const applies = rule => rule && typeof rule === 'object' && !Array.isArray(rule) && (
    (rule.kind === 'sell' && rule.destination_symbol === 'DEC' && enabledKinds.includes('sale_dec'))
    || (rule.kind === 'liquidity' && rule.dec_source === 'wallet' && enabledKinds.includes('lp_wallet'))
    || (rule.kind === 'liquidity' && rule.dec_source !== 'wallet' && enabledKinds.includes('lp_sell_half')))
    && typeof rule.source_region === 'string' && selected.some(region => region.region_uid === rule.source_region)
    && typeof rule.symbol === 'string' && (!produced || produced.has(`${rule.source_region}|${String(rule.symbol).toUpperCase()}`));
  const clean = rule => { const { reserved_dec, protected_dec_minimum, ...current } = rule; return current; };
  const isRule = rule => rule && typeof rule === 'object' && !Array.isArray(rule);
  // a saved route without an id (older versions) gets a stable one derived from what it is, so it is matched the same way on every visit
  const idOf = rule => rule.id ?? `legacy:${rule.kind}|${rule.dec_source ?? ''}|${rule.source_region}|${rule.symbol}`;
  const savedRules = Array.isArray(saved.rules) ? saved.rules : [];
  // storage is the truth; this visit's routes (kept by the caller while the modal stays open) only add what storage does not have yet (e.g. a failed save)
  const fromStorage = savedRules.filter(applies).map(rule => ({ ...clean(rule), id: idOf(rule) }));
  const have = new Set(fromStorage.map(rule => rule.id));
  const rules = [...fromStorage, ...(Array.isArray(initialRules) ? initialRules : []).filter(applies).map(rule => ({ ...clean(rule), id: idOf(rule) })).filter(rule => !have.has(rule.id))];
  const knownIds = new Set(rules.map(rule => rule.id));   // routes this screen showed: only these can be removed by this screen
  // where the player's routes are saved is always shown (status next to the route count). A browser that refuses storage is detected up front.
  const storageUsable = () => { try { const probe = 'ceres:storage-probe'; localStorage.setItem(probe, '1'); localStorage.removeItem(probe); return true; } catch (_) { return false; } };
  const removedIds = new Set();   // routes the player removed in this screen (saved as tombstones so a saved copy on another device cannot bring them back)
  const S = { saveError: '', space: { busy: false, again: false, failed: false, note: '', timer: null }, rules, busy: false, error: '', preview: null, selected: 0, protectedWalletDec: (typeof saved.protected_wallet_dec === 'string' || typeof saved.protected_wallet_dec === 'number') ? String(saved.protected_wallet_dec) : '0' };  // S10 / L4-06: only a text or number is a setting
  if (!first.ok) S.saveError = 'unreadable'; else if (!storageUsable()) S.saveError = 'blocked';
  // whenever the run side re-renders (an old finished run discarded, a run ended), pricing is looked at again.
  const run = createRoutingRun({ player, onRender: () => { onRender?.(); ensurePreview(); }, isCurrent });
  // once the harvest is confirmed, the
  // exact preview runs by itself after every edit, so the list below already shows the routes in the bundles Sign will
  // use. Sign reuses that preview. One preview at a time; a preview that failed is not retried until something changes.
  let previewTimer = null;
  let failedKey = null;
  const planKey = () => JSON.stringify([S.rules, S.protectedWalletDec]);
  function ensurePreview() {
    if (previewTimer || S.preview || S.busy || S.previewing || !receipts.length || !S.rules.length || !allowed || !signingEnabled
        || failedKey === planKey() || run.record) return;
    previewTimer = setTimeout(async () => {
      previewTimer = null;
      if (S.preview || S.busy || !(isCurrent?.() ?? true)) return;
      const key = planKey();
      S.previewing = preview().then(result => { if (planKey() === key) { S.preview = result; checkLayout(result); } },
        error => { failedKey = key; S.error = error.message || String(error); });
      S.error = '';
      notify();
      try { await S.previewing; } finally { S.previewing = null; }
      notify();
    }, 400);
  }
  // The list is grouped by the browser's port of the compiler; if the server's exact plan ever groups differently, the screen
  // keeps its layout and the difference goes to the Ceres log (stage "layout") so the port can be fixed ().
  function checkLayout(result) {
    try {
      const side = (groups) => groups.map(ids => [...new Set(ids)].sort().join(',')).join('/');
      const local = side(plannedBundles(S.rules, produced).bundles.map(bundle => bundle.operations.flatMap(op => op.routeIds)));
      const server = side((result?.manifest?.bundles || []).map(bundle => bundle.operations.flatMap(op => op.members.map(member => member.route_id))));
      if (local !== server) fetch(`/api/harvest/routing/v2/client-report?player=${encodeURIComponent(player)}`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage: 'layout', error: `local ${local.length} chars, server ${server.length} chars` }) }).catch(() => {});
    } catch (_) {}
  }
  const notify = () => { if (isCurrent?.()) onRender?.(); ensurePreview(); };

  function save() {
    // the root setup = this screen's routes + everything else in storage as it is NOW (another tab's additions included); an unreadable record is never overwritten
    const current = readRoot();
    S.saveError = current.ok ? '' : 'unreadable';
    if (current.ok) {
      try {
        const stored = Array.isArray(current.data.rules) ? current.data.rules : [];
        const byId = new Map(stored.filter(isRule).map(rule => [idOf(rule), rule]));
        const shown = new Set(S.rules.map(rule => rule.id));
        const kept = stored.filter(rule => !(isRule(rule) && applies(rule) && knownIds.has(idOf(rule)) && !shown.has(idOf(rule))));
        const merged = S.rules.map(rule => ({ ...(byId.get(rule.id) || {}), ...clean(rule) }));
        const mine = new Set(merged.map(rule => rule.id));
        const rest = kept.filter(rule => !(isRule(rule) && mine.has(idOf(rule))));
        for (const rule of S.rules) knownIds.add(rule.id);
        const dead = Array.isArray(current.data.deleted) ? current.data.deleted.filter(tomb => tomb && typeof tomb.id === 'string') : [];
        const knownDead = new Set(dead.map(tomb => tomb.id));
        const deleted = [...dead, ...[...removedIds].filter(id => !knownDead.has(id)).map(id => ({ id, at: Date.now() }))].slice(-5000);
        localStorage.setItem(key, JSON.stringify({ ...current.data, protected_wallet_dec: S.protectedWalletDec, rules: [...merged, ...rest], deleted }));
      } catch (_) { S.saveError = 'blocked'; /* storage full or blocked: the screen keeps working; the saved record is unchanged */ }
    }
    try { onRulesChange?.(S.rules.map(rule => ({ ...rule }))); } catch (_) {} // the caller keeps this visit's routes while the window stays open
    if (hasSession(player)) pushSoon();
  }

  // the optional saved space. After a sign-in every change is brought together with the saved copy (both directions, nothing lost).
  function adoptRoot() {
    const root = readRoot().data;
    const dead = new Set((Array.isArray(root.deleted) ? root.deleted : []).map(tomb => tomb?.id));
    S.rules = S.rules.filter(rule => !dead.has(rule.id));
    const have = new Set(S.rules.map(rule => rule.id));
    for (const rule of (Array.isArray(root.rules) ? root.rules : []).filter(applies).map(item => ({ ...clean(item), id: idOf(item) }))) {
      if (!have.has(rule.id) && !dead.has(rule.id)) { S.rules.push(rule); have.add(rule.id); knownIds.add(rule.id); }
    }
    S.selected = Math.max(0, Math.min(S.selected, S.rules.length - 1));
  }
  async function runSync() {
    if (S.space.busy) { S.space.again = true; return; }
    S.space.busy = true; S.space.note = '';
    try {
      do {
        S.space.again = false;
        const result = await syncRouting(player, { read: () => readRoot().data, write: root => { try { localStorage.setItem(key, JSON.stringify(root)); } catch (_) { /* storage blocked: the saved copy is still correct */ } } });
        if (result.ok) { adoptRoot(); S.space.failed = false; } else S.space.failed = true; if (!result.ok) S.space.note = result.reason === 'signed-out' ? 'Signed out. Press Save across devices to sign in again.' : 'Could not reach your saved space just now; your routes are safe on this device.';
      } while (S.space.again);
    } finally { S.space.busy = false; S.preview = null; notify(); }
  }
  function pushSoon() { clearTimeout(S.space.timer); S.space.timer = setTimeout(runSync, 800); }
  const onSpaceChanged = (event) => {   // the account menu signed in or out (and merged): show the merged routes
    if (String(event?.detail?.account || '').toLowerCase() !== String(player).toLowerCase()) return;
    adoptRoot(); S.preview = null; notify();
  };
  try { window.addEventListener('ceres:space-changed', onSpaceChanged); } catch (_) { /* no window in tests */ }
  // one status next to the route count says where the routes are saved and when they are NOT (never silent). Green: safe across devices.
  // Orange: saved on this device only (or still saving / saved space unreachable). Red: NOT saved.
  function saveStatusHtml() {
    if (!allowed) return '';
    let text, tone, tip = '';
    const device = S.saveError === 'blocked' ? 'Not saved on this device: this browser blocks storage.' : S.saveError === 'unreadable' ? 'Not saved on this device: the saved setup here could not be read.' : '';
    // "Saved across devices" is true exactly when the routes on this device equal the saved copy as last synced -- a signed-in session is not needed for that
    // and adding a route and removing it again lands back on it.
    const synced = isSynced(player, readRoot().data);
    if (synced) { text = device ? `Saved across devices. ${device}` : 'Saved across devices'; tone = device ? 'is-warn' : 'is-ok'; if (!hasSession(player)) tip = 'This device matches your saved copy. Sign in from the account menu to keep syncing changes.'; }
    else if (hasSession(player) && !S.space.failed) { text = 'Saving across devices…'; tone = 'is-warn'; }
    else if (device) { text = `${device} Your routes are lost when you close the page.`; tone = 'is-bad'; }
    else if (hasSession(player)) { text = "Saved on this device only. Can't reach your saved space."; tone = 'is-warn'; }
    else { text = 'Saved on this device'; tone = 'is-warn'; tip = 'Sign in from the account menu to keep your routing setup on every device.'; }
    return `<span class="r3-save-state ${tone}" role="status"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text)}</span>`;
  }
  function spaceActionsHtml() { return ''; }
  // a wallet-DEC liquidity route the wallet cannot fund (known from the exact preview) is offered "sell half first".
  function walletShort(rule) {
    const step = (S.preview?.steps || []).find(item => item.kind === 'liquidity' && item.rule_id === rule?.id && item.dec_source === 'wallet');
    const gap = Number(step?.dec_funding?.shortfall);
    return gap > 0 ? { needed: Number(step.dec_funding.up_to), available: Math.max(0, Number(step.dec_funding.available)) } : null;
  }
  function heldBackHtml() { const notes = [...new Set((S.preview?.steps || []).map(step => step.held_back).filter(Boolean))]; return notes.length ? `<p class="r3-reserve-note" role="status">${notes.map(esc).join(' ')}</p>` : ''; }
  function spaceNoteHtml() { return S.space.note ? `<p class="r3-space-note" role="status">${esc(S.space.note)}</p>` : ''; }

  function change(field, index, value) {
    if (S.busy || !S.rules[index] || !['symbol', 'quantity', 'source_region', 'route_kind', 'slippage'].includes(field)) return; // (: no edits while Sign is working
    S.moved = null;
    if (['symbol', 'source_region', 'route_kind'].includes(field)) { // never two routes with one region + resource + kind
      if (field === 'route_kind' && (!KIND_FIELDS[value] || !enabledKinds.includes(value))) return;
      const candidate = { ...S.rules[index], ...(field === 'route_kind' ? KIND_FIELDS[value] : { [field]: value }) };
      const next = freeRoute(candidate, field, takenBy(S.rules, index), enabledKinds);
      if (!next) { notify(); return; }
      Object.assign(S.rules[index], next);
      S.moved = next === candidate ? null : { index, text: `${regionTitle(next.source_region)} already has a ${KIND_LABEL[routeKindOf(candidate)]} route for ${tokenLabel(candidate.symbol)}, so this route is now ${KIND_LABEL[routeKindOf(next)]} for ${tokenLabel(next.symbol)}.` };
      S.preview = null; S.selected = index;
      save(); notify(); return;
    }
    if (field === 'route_kind') {
      if (!enabledKinds.includes(value)) return;
      S.rules[index].kind = value === 'sale_dec' ? 'sell' : 'liquidity';
      S.rules[index].dec_source = value === 'lp_wallet' ? 'wallet' : value === 'lp_sell_half' ? 'swap_half' : undefined;
      S.rules[index].destination_symbol = value === 'sale_dec' ? 'DEC' : undefined;
    } else S.rules[index][field] = ['quantity', 'slippage'].includes(field) ? String(value).replace(/,/g, '').trim() : value;
    if (field === 'quantity') S.rules[index].quantity_mode = 'percent';
    S.preview = null;
    S.selected = index;
    save(); notify();
  }

  async function preview() {
    if (!receipts.length) throw new Error('Confirm the harvest before reviewing route amounts.');
    if (!S.rules.length) throw new Error('Add a harvest route first.');
    if (S.rules.some(rule => isPercent(rule) && (!/^\d+$/.test(String(rule.quantity)) || Number(rule.quantity) < 1 || Number(rule.quantity) > 100))) throw new Error('Each route percent must be a whole number from 1% to 100%.');
    if (!/^(?:0|[0-9]+(?:\.[0-9]+)?)$/.test(S.protectedWalletDec)) throw new Error('Protected wallet DEC must be zero or greater.');
    const response = await fetch(`/api/harvest/routing/preview?player=${encodeURIComponent(player)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope, phase: 'after', manifest_version: 2, rules: S.rules,
        protected_wallet_dec: S.protectedWalletDec, reserves: [], slippage: '2.5', harvest_attempt_ids: receipts }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Fresh routing review unavailable.');
    if (result.blocked || !result.manifest) throw new Error((result.issues || []).join(' · ') || 'Routing review is not ready.');
    return result;
  }

  async function start() {
    if (S.busy || !allowed || !signingEnabled) return;
    S.busy = true; S.error = ''; notify();
    const pressed = planKey();
    try {
      // one press prices the routes, creates the run and signs.
      if (S.previewing) await S.previewing;
      if (!S.preview) { S.preview = await preview(); notify(); }
      // (: the plan Sign was pressed for is the plan that is signed; anything else stops before the run exists.
      if (planKey() !== pressed) throw new Error('The routes changed. Review the updated approvals and press Sign again.');
      if (isCurrent?.() ?? true) {
        try { await run.create(S.preview); }
        catch (error) {
          // a quote older than 10 minutes, or one lost to a Ceres restart, is priced again. (: the new
          // quote is shown first -- the bundles list updates -- and signing needs a fresh Sign press, never the unseen plan.
          if (!/expired or changed/i.test(error?.message || '')) throw error;
          S.preview = await preview();
          S.error = 'Prices moved while this stayed open. Review the updated approvals and press Sign again.';
          return;
        }
        S.busy = false; notify();
        await run.action('sign'); // straight to the wallet, then every next approval by itself
      }
    }
    catch (error) { S.error = error.message || String(error); }
    finally { S.busy = false; notify(); }
  }

  function action(name, node) {
    if (name === 'use-sell-half') { change('route_kind', Number(node?.dataset.r3Index), 'lp_sell_half'); return; }
    if (S.busy && ['add', 'remove'].includes(name)) return;
    if (['select', 'add', 'remove'].includes(name)) S.moved = null;
    if (name === 'select') { S.selected = Number(node?.dataset.r3Index) || 0; notify(); return; }
    if (name === 'review') { node?.closest('.harvest-panel')?.querySelector('.r3-flow-stage')?.focus(); return; }
    if (run.record && ['sign', 'check', 'stop', 'find', 'toggle-member'].includes(name)) return run.action(name, node);
    // (maintainer): a new route takes the last selected route's region, else the active region with the lowest number
    // an inactive region (no producing plot) is never prefilled, though it stays selectable.
    const defaultRegion = () => {
      const last = S.rules[S.selected]?.source_region;
      if (last && selected.some((region) => region.region_uid === last)) return last;
      return (selected.find((region) => region.active !== false) || selected[0]).region_uid;
    };
    if (name === 'add' && selected.length && allowed) {
      const taken = new Set(S.rules.map(routeKey));
      const first = defaultRegion();
      const regionOrder = [first, ...activeFirst(selected).map((region) => region.region_uid).filter((uid) => uid !== first)];
      const kindOrder = ['sale_dec', 'lp_wallet', 'lp_sell_half'].filter((kind) => enabledKinds.includes(kind));
      let pick = null;
      for (const source_region of regionOrder) {
        for (const symbol of HARVEST_ROUTE_RESOURCES) {
          const kind = kindOrder.find((value) => !taken.has(`${source_region}|${symbol}|${value}`));
          if (kind) { pick = { source_region, symbol, kind }; break; }
        }
        if (pick) break;
      }
      if (!pick) return; // every region, resource and kind already has its route
      S.rules.push({ id: globalThis.crypto?.randomUUID?.() || `route-${Date.now()}-${S.rules.length}`,
        kind: pick.kind === 'sale_dec' ? 'sell' : 'liquidity', source_region: pick.source_region, symbol: pick.symbol,
        ...(pick.kind === 'sale_dec' ? { destination_symbol: 'DEC' } : pick.kind === 'lp_wallet' ? { dec_source: 'wallet' } : { dec_source: 'swap_half' }),
        quantity_mode: 'percent', quantity: '25', slippage: '2.5' });
      S.selected = S.rules.length - 1;
      S.preview = null; save(); notify();
    }
    if (name === 'remove' && S.rules[Number(node?.dataset.r3Index)]) {
      const removed = Number(node.dataset.r3Index);
      if (S.rules[removed]?.id !== undefined) removedIds.add(S.rules[removed].id);
      S.rules.splice(removed, 1);
      if (removed < S.selected) S.selected -= 1;
      S.selected = Math.max(0, Math.min(S.selected, S.rules.length - 1)); S.preview = null; save(); notify();
    }
    if (name === 'start') return start();
  }

  // (maintainer: the first paint must already be the final layout): the list is grouped into the approvals Sign will
  // use from the first paint, by the browser's own port of the compiler's grouping (routing-layout.js, parity-tested). The
  // server preview later only fills in amounts; it never regroups the list. Routes whose resource the harvest did not
  // produce are listed last, as not routed.
  function indexHtml() {
    const chip = (index) => ruleChip(S.rules[index], index, index === S.selected, estimateFor(S.rules[index], S.preview?.steps));
    const { bundles, unplaced } = plannedBundles(S.rules, produced);
    const byId = new Map(S.rules.map((rule, index) => [rule.id, index]));
    const placed = new Set();
    const total = bundles.length;
    const blocks = bundles.map((bundle) => {
      const indexes = [...new Set(bundle.operations.flatMap((operation) => operation.routeIds.map((id) => byId.get(id))))]
        .filter((index) => index != null && !placed.has(index));
      indexes.forEach((index) => placed.add(index));
      return `<div class="r3-bundle-head">Approval ${bundle.approval} of ${total} · ${bundle.lp ? 'LP deposits' : 'Sales'} · ${indexes.length} route${indexes.length === 1 ? '' : 's'}</div>${indexes.map(chip).join('')}`;
    });
    const rest = displayOrder(S.rules).filter((index) => !placed.has(index));
    return blocks.join('') + (rest.length ? `<div class="r3-bundle-head">Nothing harvested · not routed</div>${rest.map(chip).join('')}` : '');
  }

  function html() {
    if (run.record) return run.html();
    const rule = S.rules[S.selected];
    const shares = plannedShares(S.rules, S.selected);
    const sourceCount = new Set(S.rules.map(item => item.source_region)).size;
    const estimate = estimateFor(rule || {}, S.preview?.steps);
    const tooltip = 'Posting is your everyday Hive key; it cannot move tokens out of your account. Money transfers need the Active key.';
    return `<section class="r3-setup" aria-label="Harvest routing setup"><div class="r3-summary"><strong>${S.rules.length} route${S.rules.length === 1 ? '' : 's'}</strong><span>${sourceCount} source region${sourceCount === 1 ? '' : 's'}</span>${saveStatusHtml()}${routeScrollCueHtml()}<label class="r3-protected-setting"><span>Protected wallet DEC</span><input type="text" inputmode="decimal" value="${esc(S.protectedWalletDec)}" data-r3-setting="protected_wallet_dec" aria-label="Protected wallet DEC" class="ceres-wide-input" data-control-density="comfortable"></label><span class="r3-summary-line" data-tip="${esc(tooltip)}" aria-label="Signs with your Posting key. ${esc(tooltip)}" tabindex="0">Signs with your Posting key</span></div>${receipts.length ? '' : `<div class="r3-pre-harvest" role="note"><span>Routing is signed after your harvest is confirmed. Add the routes you want here, then continue to the harvest.</span><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-hm="routing-back">Continue to harvest</button></div>`}<div class="r3-flow-layout">${routeListColumnHtml(indexHtml())}<div class="r3-flow-stage" tabindex="-1"><div class="r3-stage-head"><h3>${rule ? 'Review this route' : 'Add a route'}</h3><span>${S.preview ? 'Confirmed harvest estimate · pre-sign review' : 'Estimated output · pre-sign review'}</span></div>${rule ? `${plannedFlow(shares, S.preview?.steps)}${ruleHtml(rule, selected, S.selected, enabledKinds, estimate, receipts.length > 0, takenBy(S.rules, S.selected), S.moved?.index === S.selected ? S.moved.text : '', walletShort(rule), offerOnlyKinds)}` : '<p>Choose a source region and resource to begin.</p>'}<div class="r3-stage-status">${S.preview ? 'Review the confirmed harvest amounts, then continue to approvals.' : 'Review exact shares before approval.'}${receipts.length ? '' : '<small>Harvest first; approvals follow confirmed receipts.</small>'}</div></div></div><template class="r3-actions">${spaceActionsHtml()}<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-r3="add"${!allowed || !selected.length || S.busy ? ' disabled' : ''}>Add route</button>${withWalletChooser(player, receipts.length ? `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-r3="start"${!S.rules.length || !signingEnabled || S.busy ? ' disabled' : ''}>Sign</button>` : '')}</template>${spaceNoteHtml()}${heldBackHtml()}${S.error ? `<p class="r3-error" role="alert">${esc(S.error)}</p>` : ''}${!signingEnabled ? '<p>Routing signatures are currently disabled on the server.</p>' : ''}</section>`;
  }

  async function load() {
    const record = await run.load();
    // A stopped run that never reached a wallet leaves this harvest free to route again: show the setup.
    const neverSent = record?.state === 'stopped' && Object.values(record.bundles || {})
      .every(status => ['stopped', 'skipped'].includes(status.state) && !status.tx_id && !status.claim);
    if (record && (!receipts.length || neverSent || JSON.stringify(record.harvest_attempt_ids || []) !== JSON.stringify(receipts))) run.discardFinished();
    ensurePreview(); // after the old run is gone, so a stored finished run no longer blocks the automatic pricing
  }

  return { get busy() { return S.busy || run.busy; }, get runId() { return run.runId; }, get record() { return run.record; },
    html, action, change, setting: (field, value) => { if (field === 'protected_wallet_dec') { S.protectedWalletDec = String(value).replace(/,/g, '').trim(); S.preview = null; save(); notify(); } }, load, detach: () => { try { window.removeEventListener('ceres:space-changed', onSpaceChanged); } catch (_) { /* no window */ } run.detach(); } };
}
