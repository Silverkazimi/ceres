import { balancesChanged, onBalancesChanged } from './balances-bus.js';
import { territorySlug, territoryCellHtml } from './icons.js';
import { RESOURCE_ORDER, resourceKeys, harvestResourceColumns, singlePlotHarvestLedger, observeHarvestResourceDividers, syncHarvestResourceDividers } from './harvest-resources.js';
import { netAfterCosts } from './harvest-net.js';
import { initCustomSelect } from './customSelect.js';
import { createHarvestRouting, hasSavedHarvestRouting, mintHarvestRunId, routingClientRunId } from './harvest-routing.js';
import { createRoutingSetup, loadEnabledRoutingKinds } from './routing-setup.js';
import { drawRoutePointers, patchRoutingWindow, scrollRouteIndexByPage, scrollRouteIndexToActive, updateRouteScrollCue } from './routing-window.js';
import { harvestSuccessHtml, harvestSuccessHeadingHtml } from './harvest-results.js';
import { batchBadgeHtml, batchLedgerRows, batchSlotsHtml, batchRegionPlaqueHtml, batchRegionLedgerHtml, batchTotalHtml } from './harvest-ledger.js';
import { COMMIT_ICON } from './button-icons.js';
import { tipAttrs } from './tooltip.js';
import { walletReady, walletKind, walletControlsHtml, walletSigningStatus, clearStaleWalletRequest, layoutOpenWalletDrawers } from './wallet.js';
// the one shared harvest modal: single plot, research plot, and the header
// batch (All regions / One region / Selection). Built to the approved modal mockups.
// Broadcasting is behind the server's harvest_signing_enabled flag (OFF): the primary button
// reads "Signing not yet enabled — awaiting first on-chain test" until the maintainer's §10 test.
import { ART_REV } from './art.js';
import { requestSignatureThroughGate, prepareSigningBatch } from './broadcast-gate.js';
import { BOARD, CURRENT_PLAYER, playerQueryAppend } from './board.js';
import { esc, formatPlotLabel } from './format.js';
import { ASSET_BASE } from './state.js';
import {
  advanceProjection, capacityLevel, fmt2, fmtDuration, fmtPct, noteServerNow, plotHarvest, plotTaxes, plotTaxesError, refreshPlotTaxes,
  refreshPlotHarvest, subscribeTick, tokenIcon, tokenLabel,
} from './harvest-state.js';
import { openWorksiteFeedModal } from './worksite.js';
import { plaque as regionPlaque } from './wallet-resources.js';
import { resourceSymbolsWhere } from './resources.js';

const SIGNING_OFF_TEXT = 'Signing not yet enabled — awaiting first on-chain test';
const HARVEST_ROUTING_ENABLED = false;
const COST_ORDER = resourceSymbolsWhere('cost_input'); // registry: GRAIN WOOD STONE IRON
const TOKEN_ORDER = RESOURCE_ORDER;
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

function affordabilityRow(affordability, symbol) {
  return (affordability?.rows || []).find((row) => row.symbol === symbol) || null;
}

const M = {
  open: false, gen: 0, mode: 'batch', scopeKind: 'all', deedUids: [],
  selectedRegions: new Set(), state: null, preview: null, loading: false, error: null,
  expanded: new Set(), returnFocus: null, unsubscribe: null, run: null, player: null, openId: 0, walletControlId: null,
  selectedTaxes: new Set(), taxInit: false, taxAsked: new Set(), reviewing: null, routing: null, routingRunId: null, routingTerminal: false, harvestPreparing: false,
  v2: null, v2Kinds: [], v2OfferOnly: [], v2Signing: false,
  regionLayout: null, costsOpen: new Set(),
};

function overlay() {
  let el = document.getElementById('harvest-modal-overlay');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'harvest-modal-overlay';
  el.className = 'harvest-overlay';
  el.hidden = true;
  el.innerHTML = '<div class="harvest-panel" role="dialog" aria-modal="true" aria-labelledby="harvest-modal-title"></div>';
  // the per-route pointers follow the list's scroll, late card art and window resizes.
  const redrawPointers = (event) => { if (!event || event.target?.closest?.('.r3-window, .r3-setup')) { drawRoutePointers(el); if (event?.target?.classList?.contains('r3-route-index')) updateRouteScrollCue(el); } };
  el.addEventListener('click', (event) => { const more = event.target?.closest?.('[data-r3-more]'); if (more && !more.disabled) scrollRouteIndexByPage(more); });
  el.addEventListener('scroll', redrawPointers, true);
  el.addEventListener('load', redrawPointers, true);
  window.addEventListener('resize', () => { if (!el.hidden) { updateRouteScrollCue(el); drawRoutePointers(el); } });
  onBalancesChanged(({ assets }) => { if (M.open && !M.run?.busy && !M.routing?.busy && assets.some(a => ['DEC', 'SPS', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'].includes(a))) { loadState(); loadPreview(); } }, { player: () => M.player });
  el.addEventListener('click', (event) => {
    if (event.target === el && !M.run?.busy && !M.routing?.busy) { closeHarvestModal(); return; }
    const v2Action = event.target.closest('[data-r3]');
    if (v2Action && v2Action.dataset.r3 !== 'toggle-member') { M.v2?.action(v2Action.dataset.r3, v2Action); return; }
    const decSource = event.target.closest('[data-hr-dec-source]');
    if (decSource) { M.routing?.change(decSource); return; }
    const routingAction = event.target.closest('[data-hr]');
    if (routingAction) { M.routing?.action(routingAction.dataset.hr, routingAction); return; }
    const action = event.target.closest('[data-hm]');
    if (action) handleAction(action, event);
  });
  el.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-r3="toggle-member"]')) { M.v2?.action('toggle-member', target); return; }
    if (target.matches('[data-r3-setting]')) { M.v2?.setting(target.dataset.r3Setting, target.value); return; }
    if (target.matches('[data-r3-field]')) { M.v2?.change(target.dataset.r3Field, Number(target.dataset.r3Index), target.value); return; }
    if (target.matches('[data-hr-field], [data-hr-reserve], [data-hr-member]')) { M.routing?.change(target); return; }
    if (target.matches('[data-hm-region-check]')) {
      const uid = target.value;
      if (target.checked) M.selectedRegions.add(uid); else M.selectedRegions.delete(uid);
      render();
    }
    if (target.matches('[data-hm-tax-check]')) {
      if (target.checked) M.selectedTaxes.add(target.value); else M.selectedTaxes.delete(target.value);
      render();
    }
  });
  // Escape closes from anywhere while the modal is open (focus can sit outside the panel
  // after a re-render); Tab is trapped inside the panel.
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && M.open && !M.run?.busy && !M.routing?.busy) { event.preventDefault(); if (M.v2) { M.v2.detach(); M.v2 = null; render(); } else closeHarvestModal(); }
  });
  // S10 / L4-07: a route chip is a focusable control; Enter and Space activate it like a click (keyboard users could not pick another approval).
  el.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const chip = event.target?.closest?.('.r3-route-chip[role="button"]');
    if (!chip || chip.tagName === 'BUTTON') return;
    event.preventDefault();
    chip.click();
  });
  el.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const focusable = [...el.querySelectorAll('button:not([disabled]), select, input, [tabindex="0"]')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.body.appendChild(el);
  return el;
}

export function initHarvest() {
  document.getElementById('harvest-btn')?.addEventListener('click', () => openHarvestModal({}));
  document.getElementById('mobile-harvest-btn')?.addEventListener('click', () => openHarvestModal({}));
  // The plot bars' sickle buttons live inside the board; one delegated listener serves them all.
  document.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-harvest-action="open-plot"]');
    if (chip) { event.stopPropagation(); openHarvestModal({ deedUid: chip.dataset.deedUid }); return; }
    // a finished build's harvest box -- feed the workers via the existing worksite modal.
    const feed = event.target.closest('[data-harvest-action="feed-workers"]');
    if (feed) { event.stopPropagation(); openWorksiteFeedModal(feed.dataset.deedUid); }
  }, true);
}

export function openHarvestModal({ deedUid = null } = {}) {
  const el = overlay();
  M.returnFocus = document.activeElement;
  M.gen += 1;
  M.player = CURRENT_PLAYER || BOARD?.player || null;
  M.preview = null; M.error = null; M.run = null; M.reviewing = null; M.routing = null; M.routingRunId = null; M.routingTerminal = false; M.harvestPreparing = false; M.expanded = new Set(); M.v2 = null; M.v2Kinds = []; M.v2Signing = false;
  M.routeDrafts = []; // routes added in this visit only; never stored, so they cannot outlive the open window
  M.regionLayout = batchRegionLayout(); M.costsOpen = new Set();
  M.selectedTaxes = new Set(); M.taxInit = false; M.taxAsked = new Set();
  if (deedUid) {
    M.mode = 'plot'; M.scopeKind = 'plot'; M.deedUids = [deedUid];
    M.selectedRegions = new Set();
  } else {
    M.mode = 'batch'; M.scopeKind = 'all'; M.deedUids = []; M.selectedRegions = new Set();
  }
  M.open = true;
  M.openId += 1;
  M.walletControlId = `harvest-${M.openId}`;
  // on the phone Harvest is part of the page (the page scrolls, so the browser bar can hide); the board's
  // scroll position is kept and restored on close.
  const phone = document.documentElement.classList.contains('ceres-lw-le-1279');
  if (phone && el.hidden) M.boardScrollY = window.scrollY;
  el.hidden = false;
  if (phone) window.scrollTo(0, 0);
  render();
  el.querySelector('.harvest-x')?.focus();
  M.unsubscribe?.();
  M.unsubscribe = subscribeTick(onTick);
  loadState();
  if (!deedUid) loadRoutingKinds();
  if (deedUid) refreshPlotHarvest(deedUid, { force: !plotHarvest(deedUid) }).then(() => { if (M.open && (!M.run || M.routing)) render(); });
  loadPreview();
}

export async function closeHarvestModal() {
  if (M.routing?.busy && M.run?.phase !== 'verification_incomplete') return;
  M.v2?.detach();
  M.v2 = null;
  const account = M.player;
  const routing = M.routing;
  const runId = routing?.runId || M.routingRunId;
  const terminal = routing ? routing.terminal : M.routingTerminal;
  const preserveHarvestRun = Boolean(M.routingRunId && (M.run || M.harvestPreparing));
  routing?.clear?.();
  M.routing = null;
  M.routingRunId = null;
  M.routingTerminal = false;
  M.error = null;
  const el = document.getElementById('harvest-modal-overlay');
  const wasOpen = el && !el.hidden;
  if (el) el.hidden = true;
  if (wasOpen && M.boardScrollY != null) { const y = M.boardScrollY; M.boardScrollY = null; requestAnimationFrame(() => window.scrollTo(0, y)); }
  M.open = false;
  M.reviewing = null;
  M.gen += 1;
  M.unsubscribe?.();
  M.unsubscribe = null;
  const target = M.returnFocus?.isConnected ? M.returnFocus : document.getElementById('harvest-btn');
  target?.focus?.();
  if (runId && !terminal && !preserveHarvestRun) {
    try {
      const response = await fetch('/api/harvest/routing/stop?player=' + encodeURIComponent(account), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId }), keepalive: true,
      });
      if (!response.ok) console.error('CERES_ROUTING_STOP_FAILED', runId);
    }
    catch (error) { console.error('CERES_ROUTING_STOP_FAILED', error); }
  }
}

export function harvestModalIsOpen() {
  return M.open;
}

export function harvestModalCanClose() {
  return M.open && !M.run?.busy && !M.routing?.busy;
}

async function loadRoutingKinds() {
  const openId = M.openId;
  const account = M.player;
  try {
    const result = await loadEnabledRoutingKinds(account);
    if (!M.open || M.openId !== openId || M.player !== account) return;
    M.v2Kinds = result.enabled_kinds.filter(kind => ['sale_dec', 'lp_wallet', 'lp_sell_half'].includes(kind));
    M.v2OfferOnly = Array.isArray(result.offer_only_kinds) ? result.offer_only_kinds : [];
    M.v2Signing = result.signing_enabled;
    render();
  } catch (_) {
    if (M.open && M.openId === openId) { M.v2Kinds = []; M.v2Signing = false; }
  }
}

async function loadState() {
  // Its own token: loadPreview() bumps M.gen right after, which must not discard this read.
  const openId = M.openId;
  const current = () => M.open && openId === M.openId;
  try {
    const resp = await fetch(playerQueryAppend('/api/harvest/state'));
    const data = await resp.json();
    if (!current()) return;
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    noteServerNow(data.server_now);
    M.state = data;
    render();
  } catch (error) {
    if (current()) { M.error = `Harvest data unavailable: ${error.message || error}`; render(); }
  }
}

// two batch tabs, "All regions" and "Regions" (checkbox list). Both read the same fresh
// all-regions quote once; ticking a region only re-derives totals, never refetches or re-lays out.
function currentScope() {
  if (M.mode === 'plot') return { kind: 'deeds', deed_uids: M.deedUids };
  return { kind: 'all' };
}

async function loadPreview() {
  const scope = currentScope();
  M.gen += 1;
  const gen = M.gen;
  M.preview = null;
  M.error = null;
  M.loading = !!scope;
  render();
  if (!scope) return;
  try {
    const resp = await fetch(playerQueryAppend('/api/harvest/preview'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope }),
    });
    const data = await resp.json();
    if (gen !== M.gen) return;
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    noteServerNow(data.server_now);
    M.preview = data;
  } catch (error) {
    if (gen === M.gen) M.error = `Fresh harvest review unavailable: ${error.message || error}`;
  } finally {
    if (gen === M.gen) { M.loading = false; render(); }
  }
}

function handleAction(el, event) {
  const action = el.dataset.hm;
  if (action === 'routing-back') { M.v2?.detach(); M.v2 = null; render(); return; }
  if (action === 'recover-harvest') { recoverHarvest(); return; }
  if (action === 'routing') { openRouting(); return; }
  // (maintainer: "Closing the routing window closes ALL harvest screens instead of returning to the previous harvest
  // screen"): while routing is open, X (and Escape) go back to the harvest screen it came from, like "Back to harvest".
  if (action === 'close') { if (M.v2) { M.v2.detach(); M.v2 = null; render(); return; } closeHarvestModal(); return; }
  if (action === 'show-costs') {
    const uid = el.dataset.region || 'total';
    if (M.costsOpen.has(uid)) M.costsOpen.delete(uid); else M.costsOpen.add(uid);
    render();
    return;
  }
  if (action === 'scope') {
    const kind = el.dataset.kind;
    if (kind === M.scopeKind || !['all', 'regions', 'taxes'].includes(kind)) return;
    M.scopeKind = kind;
    M.expanded = new Set();
    render();
    return;
  }
  if (action === 'tax-kind') {
    // one button per kind -- selects every claimable Keep (Castle), or deselects them when all are ticked.
    const deeds = taxPlots().filter((p) => p.kind === el.dataset.kind && taxHeld(p.deed_uid).length).map((p) => p.deed_uid);
    const all = deeds.length && deeds.every((d) => M.selectedTaxes.has(d));
    deeds.forEach((d) => { if (all) M.selectedTaxes.delete(d); else M.selectedTaxes.add(d); });
    render();
    return;
  }
  if (action === 'toggle-region') {
    const uid = el.dataset.region;
    if (M.expanded.has(uid)) M.expanded.delete(uid); else M.expanded.add(uid);
    render();
    return;
  }
  if (action === 'funded-only') {
    const funded = (M.preview?.regions || []).filter((r) => r.status === 'included').map((r) => r.region_uid);
    M.scopeKind = 'regions'; M.mode = 'batch'; M.deedUids = [];
    M.selectedRegions = new Set(funded);
    render();
    return;
  }
  if (action === 'confirm') { event.preventDefault(); if (el.getAttribute('aria-disabled') !== 'true') confirmHarvest(); return; }
  if (action === 'stop' && M.run) { M.run.stop = true; render(); }
  if (action === 'check-now' && M.run?.pollNow) { M.run.pollNow(); return; }
  if (action === 'refresh') { if (M.deedUids[0]) refreshPlotHarvest(M.deedUids[0], { force: true }); loadPreview(); }
}

// rendering

function tok(token, amount, { small = true } = {}) {
  const icon = tokenIcon(token);
  return `<span class="hm-tok">${icon ? `<img src="${icon}" alt="">` : ''}${amount != null ? `<b>${fmt2(amount)}</b>` : ''}${small ? `<small>${esc(tokenLabel(token))}</small>` : ''}</span>`;
}

function sortedTokens(obj) {
  return Object.keys(obj || {}).sort((a, b) => (TOKEN_ORDER.indexOf(a) < 0 ? TOKEN_ORDER.length : TOKEN_ORDER.indexOf(a)) - (TOKEN_ORDER.indexOf(b) < 0 ? TOKEN_ORDER.length : TOKEN_ORDER.indexOf(b)) || a.localeCompare(b));
}

function regionTitle(r) {
  return `Region ${esc(r.region_number || '')}`;
}

function isTaxCollection() {
  return M.scopeKind === 'taxes' || (M.mode === 'plot' && String(BOARD?.plots.find((p) => p.deed_uid === M.deedUids[0])?.resource || '').toUpperCase() === 'TAX');
}

function titleHtml() {
  if (M.v2) return `<h2 id="harvest-modal-title">Harvest routing</h2><p>${esc(M.player)}</p>`;
  if (M.routing) return `<h2 id="harvest-modal-title">Harvest &amp; routing</h2><p>${esc(M.player)}</p>`;
  if (M.run?.phase === 'success') return harvestSuccessHeadingHtml(M.run, { tax: isTaxCollection(), player: M.player });
  if (M.run?.phase === 'verification_incomplete') return `<h2 id="harvest-modal-title">${M.run.txId ? 'Harvest sent' : 'Harvest outcome unknown'} — verification incomplete</h2>`;
  const player = esc(M.player || '');
  if (M.mode === 'plot') {
    const row = BOARD?.plots.find((p) => p.deed_uid === M.deedUids[0]);
    const label = row ? formatPlotLabel(row.plot) : M.deedUids[0];
    const sub = [row?.terrain, row?.worksite_type, M.player].filter(Boolean).map(esc).join(' · ');
    const isTax = String((BOARD?.plots.find((p) => p.deed_uid === M.deedUids[0]) || {}).resource || '').toUpperCase() === 'TAX';
    return `<h2 id="harvest-modal-title">${isTax ? 'Collect taxes · plot' : 'Harvest plot'} ${esc(label)}</h2><p>${sub}</p>`;
  }
  if (M.scopeKind === 'taxes') {
    const n = chosenTaxDeeds().length;
    return `<h2 id="harvest-modal-title">Collect taxes</h2><p>${player} · <span data-hm-count>${n} Keep${n === 1 ? '' : 's'}/Castle${n === 1 ? '' : 's'} ticked</span><span class="hm-sub-more"> · everything each one holds</span></p>`;
  }
  if (M.scopeKind === 'all') return `<h2 id="harvest-modal-title">Harvest all regions</h2><p>${player} · all <span class="hm-sub-more">your </span>eligible plots<span class="hm-sub-more"> in these regions at execution</span></p>`;
  const n = M.selectedRegions.size;
  return `<h2 id="harvest-modal-title">Harvest regions</h2><p>${player} · <span data-hm-count>${n} region${n === 1 ? '' : 's'} ticked</span><span class="hm-sub-more"> · all your eligible plots in them at execution</span></p>`;
}

function scopeHtml() {
  if (M.mode === 'plot') return ''; // a plot opened from its sickle shows just that plot
  const seg = (kind, label) => `<button type="button" class="hm-seg${M.scopeKind === kind ? ' is-on' : ''} ceres-btn ceres-btn--choice" data-control-density="compact" data-hm="scope" data-kind="${kind}" aria-pressed="${M.scopeKind === kind}"><span class="ceres-btn__check" aria-hidden="true"></span>${label}</button>`;
  const hint = M.scopeKind === 'regions' ? '<span class="hm-dim">Tick the regions to harvest — each is one whole-region operation, up to 4 per signature.</span>'
    : M.scopeKind === 'taxes' ? '<span class="hm-dim">Tick the Keeps and Castles to collect — one operation each, up to 4 per signature.</span>' : '';
  return `<div class="hm-scope"><div class="hm-segs" role="group" aria-label="Harvest scope">${seg('all', 'All regions')}${seg('regions', 'Regions')}${seg('taxes', 'Taxes')}</div>${hint}</div>`;
}

function capacityCard(a, pending = false) {
  if (!a?.known) return '';
  if (pending) return '<div class="hm-card"><div class="hm-cap" data-level="ok" style="--pct:0%"><div class="hm-cap-top"><span class="hm-lbl">Worksite capacity</span><b><span class="hm-skel is-num" aria-hidden="true"></span></b><span class="hm-dim">· holds 1 week of production</span></div><span class="hm-meter"><i></i></span></div></div>';
  const level = capacityLevel(a.pct);
  const rest = a.full ? '· production paused · holds 1 week of production' : `· full in ${fmtDuration(a.hoursToFull)} · holds 1 week of production`;
  return `<div class="hm-card"><div class="hm-cap" data-level="${level}" data-hl="cap" style="--pct:${a.pct.toFixed(2)}%"><div class="hm-cap-top"><span class="hm-lbl">Worksite capacity</span><b data-hl="cap-pct">${fmt2(a.pct)}% full</b><span class="hm-dim" data-hl="cap-rest">${rest}</span></div><span class="hm-meter"><i></i></span></div></div>`;
}

// a Keep/Castle's claim view -- every resource it holds, the total and its storage fill; no costs.
function taxClaimHtml(deedUid, row) {
  const t = plotTaxes(deedUid);
  if (!t) {
    // a failed or just-tried read resolved at once with no data, re-rendered, re-read ... forever and froze
    // the page. Read once per open; redraw only when the read brought data or an error.
    const error = plotTaxesError(deedUid);
    if (error) return `<div class="hm-card"><p class="hm-dim">Tax read unavailable (${esc(error)}) — close and reopen to try again.</p></div>`;
    if (M.taxReading !== deedUid) {
      M.taxReading = deedUid;
      refreshPlotTaxes(deedUid, { force: true }).then(() => {
        if (M.taxReading === deedUid) M.taxReading = null;
        if (M.deedUids?.[0] === deedUid && (plotTaxes(deedUid) || plotTaxesError(deedUid))) render();
      });
    }
    return '<div class="hm-card"><p class="hm-dim">Reading the collected taxes…</p></div>';
  }
  const held = (t.tokens || []).filter((item) => Number(item.balance) > 0);
  const capacity = Number(t.capacity) || 0;
  const pct = capacity > 0 ? Math.min(100, (Number(t.total) / capacity) * 100) : 0;
  const name = String(row?.worksite_type || '').toUpperCase() === 'KEEP' ? 'keep' : 'castle';
  const list = held.slice().sort((a, b) => resourceKeys({ [a.token]: 1, [b.token]: 1 }).indexOf(a.token) - resourceKeys({ [a.token]: 1, [b.token]: 1 }).indexOf(b.token)).map((item) => `<div class="hm-total">${tokenIcon(item.token) ? `<img src="${tokenIcon(item.token)}" alt="">` : ''}<span><b>${fmt2(item.balance)}</b><small>${esc(tokenLabel(item.token))}</small></span></div>`).join('');
  const body = held.length
    ? `<h3 class="hm-sec-h">You collect · ${held.length} resource${held.length === 1 ? '' : 's'}</h3><div class="hm-totals">${list}</div><p class="hm-hint">Every resource is collected at once. A tax claim has no costs.</p>`
    : `<p>Nothing collected yet — this ${name} holds no taxes right now.</p>`;
  return `<div class="hm-card hm-resource-summary">${body}</div><div class="hm-card"><div class="hm-cap" data-level="ok" style="--pct:${pct.toFixed(2)}%"><div class="hm-cap-top"><span class="hm-lbl">Tax storage</span><b>${fmt2(pct)}% full</b><span class="hm-dim">· ${fmt2(Number(t.total))} of ${fmt2(capacity).replace(/\.00$/, '')}</span></div><span class="hm-meter"><i></i></span></div></div>`;
}

function plotBodyHtml() {
  const data = plotHarvest(M.deedUids[0]);
  const row = BOARD?.plots.find((p) => p.deed_uid === M.deedUids[0]);
  // the snapshot only lays the view out; every number waits for the fresh plot read.
  const p = data?.projection || row?.harvest;
  const pending = !data?.projection;
  if (!p) return `<div class="hm-card"><p class="hm-dim">${M.error ? esc(M.error) : 'Reading this plot’s harvest…'}</p></div>`;
  if (p.state === 'tax_collection') return taxClaimHtml(M.deedUids[0], row);
  if (!['harvesting', 'idle'].includes(p.state)) {
    const msg = { tax_collection: 'Tax collection is separate and not part of harvest.', construction: 'Under construction — nothing to harvest yet.', no_project: 'No worksite on this plot.' }[p.state] || 'Harvest data unavailable.';
    return `<div class="hm-card"><p>${esc(msg)}</p>${p.state === 'unavailable' ? '<button type="button" class="hm-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="refresh">Refresh</button>' : ''}</div>`;
  }
  const a = advanceProjection(p);
  const SK = '<span class="hm-skel is-num" aria-hidden="true"></span>';
  const n2 = (v) => (pending ? SK : fmt2(v));
  const icon = tokenIcon(p.token);
  const token = tokenLabel(p.token);
  const bal = data?.region_balances || null;
  const costRows = [...new Map((p.costs || []).map(c => [c.symbol, c])).values()]
    .sort((x, y) => COST_ORDER.indexOf(x.symbol) - COST_ORDER.indexOf(y.symbol)).map((c) => {
    const now = a.costs[c.symbol];
    const have = bal ? bal[c.symbol] : null;
    const affordability = affordabilityRow(data?.affordability, c.symbol);
    const available = affordability?.available ?? have;
    const after = affordability?.after ?? (available != null && now != null ? available - now : null);
    const short = Number(affordability?.short || 0) > 0;
    const afterCell = after == null ? '<td data-label="After harvest">—</td>' : short ? `<td data-label="After harvest" class="hm-need" data-hl="after-${c.symbol}">Short ${fmt2(affordability.short)} before harvest</td>` : `<td data-label="After harvest" class="hm-okc" data-hl="after-${c.symbol}">${fmt2(after)}</td>`;
    if (pending) return `<tr><td>${tok(c.symbol, null)}</td><td data-label="Region balance">${SK}</td><td data-label="After harvest">${SK}</td></tr>`;
    return `<tr><td>${tok(c.symbol, null)}</td><td data-label="Region balance">${available == null ? 'Unavailable' : fmt2(available)}</td>${afterCell}</tr>`;
  }).join('');
  const discount = Number(p.food_discount || 0);
  const names = (row?.food_discount_cards || []).map((c) => c.name).filter(Boolean);
  const discNote = discount < 0
    ? `Includes <b>−${fmt2(-discount * 100)}%</b> grain cost discount${names.length ? ` (${esc(names.join(', '))})` : ''}. Costs are calculated at harvest.`
    : 'No cost discount on this plot. Costs are calculated at harvest.';
  const f = p.finds || {};
  const frag = f.fragment_eligibility === 'eligible'
    ? `<div class="hm-find"><img src="${ASSET_BASE}/totems/totem-common.png" alt=""><span><b data-hl="frag">${pending ? SK : fmtPct(a.fragment)}</b><small>Totem fragment chance</small></span></div>` : '';
  const ll = f.labors_luck_eligibility === 'eligible'
    ? `<div class="hm-find"><img src="${ASSET_BASE}/abilities/ability_labors-luck.png" alt=""><span><b data-hl="ll">${pending ? SK : fmtPct(a.laborsLuck)}</b><small>Labor's Luck card chance</small></span></div>` : '';
  const finds = frag || ll ? `<div class="hm-card"><h3 class="hm-sec-h">Possible finds</h3><div class="hm-finds">${frag}${ll}</div><p class="hm-hint">Chances, not predictions.</p></div>` : '';
  const nextOp = Date.parse(p.next_op_allowed_date || '');
  const cooldown = Number.isFinite(nextOp) && nextOp > Date.now() ? `<div class="hm-alert is-info">Next harvest allowed in ${fmtDuration((nextOp - Date.now()) / 3.6e6)}.</div>` : '';
  const short = data?.affordability?.status === 'short'
    ? `<div class="hm-alert"><span aria-hidden="true">⚠</span><span><b>Region ${esc(p.region_uid?.split('-').pop().padStart(3, '0'))} is short ${Object.entries(data.affordability.short).map(([s, v]) => `${fmt2(v)} ${tokenLabel(s).toLowerCase()}`).join(', ')}.</b> This harvest cannot be paid from the region's available resources.</span></div>` : '';
  const issues = (data?.issues || []).length ? `<p class="hm-hint">${esc(data.issues.join(' · '))}</p>` : '';
  // the exact operation Keychain will be asked to sign, visible before Confirm.
  const chunk = M.preview?.chunks?.[0];
  const opsHtml = chunk ? `<details class="hm-ops"${M.preview.signing_enabled ? ' open' : ''}><summary>Exact Hive operation · 1 transaction · Posting key · ${esc(M.player || '')}</summary><pre>${esc(JSON.stringify(chunk.operations.map(([name, body]) => [name, { ...body, json: JSON.parse(body.json) }]), null, 2))}</pre></details>` : '';
  return `${short}${cooldown}<div class="hm-card hm-resource-summary"><h3 class="hm-sec-h">You receive · after tax</h3>${singlePlotHarvestLedger({ token: p.token, net: a.net, costs: a.costs }, { pending })}${p.is_sps ? '<p class="hm-hint">SPS is an estimate from the pool share.</p>' : ''}</div><div class="hm-grid2"><div class="hm-card"><details class="hm-balance-details"><summary>Regional balances after harvest</summary><div class="hm-table-wrap"><table class="hm-table hm-plot-costs"><thead><tr><th>Resource</th><th>Available</th><th>After harvest</th></tr></thead><tbody>${costRows || '<tr><td colspan="5">No cost</td></tr>'}</tbody></table></div><p class="hm-disc">${discNote}</p></details></div>${finds}</div>${capacityCard(a, pending)}${opsHtml}${issues}`;
}

// the region list and plot counts come from the board already in the page, so the
// rows exist at first paint; numbers appear only from the fresh (after-tax) game quote.
function boardRegions() {
  const map = new Map();
  (BOARD?.plots || []).forEach((p) => {
    if (!p.region) return;
    const e = map.get(p.region) || { region_uid: p.region, region_number: String(p.region).split('-').pop().padStart(3, '0'), plots: 0, tax_collection: [], active: false };
    e.plots += 1;
    if (Number(p.harvest_pp) > 0) e.active = true; // a region is active when one of its plots produces
    const kind = String(p.worksite_type || '').toLowerCase();
    if (kind === 'keep' || kind === 'castle') e.tax_collection.push({ deed_uid: p.deed_uid, worksite_type: p.worksite_type });
    map.set(p.region, e);
  });
  return [...map.values()].sort((a, b) => a.region_number.localeCompare(b.region_number));
}

function batchRegionLayout() {
  const regions = boardRegions();
  const byUid = new Map(regions.map((region) => [region.region_uid, { ...region, resources: new Set() }]));
  (BOARD?.plots || []).forEach((plot) => {
    const region = byUid.get(plot.region);
    if (!region || Number(plot.harvest_pp ?? plot.base_pp ?? 0) <= 0) return;
    const symbol = String(plot.resource || '').toUpperCase();
    if (!symbol || symbol === 'TAX') return;
    region.resources.add(symbol);
    ['GRAIN', 'WOOD', 'STONE', 'IRON'].forEach((input) => region.resources.add(input));
  });
  const rows = [...byUid.values()].map((region) => ({ ...region, resources: [...region.resources] }));
  const resources = resourceKeys(Object.fromEntries(rows.flatMap((region) => region.resources.map((symbol) => [symbol, true]))));
  return { rows, resources };
}

function regionSource() {
  const base = boardRegions();
  if (!M.preview) return { fresh: false, rows: base.map((r) => ({ ...r, status: 'checking' })) };
  const byUid = new Map(M.preview.regions.map((r) => [r.region_uid, r]));
  const rows = base.map((r) => ({ ...r, ...(byUid.get(r.region_uid) || { status: 'nothing' }), plots: r.plots, tax_collection: r.tax_collection }));
  M.preview.regions.forEach((r) => { if (!base.some((b) => b.region_uid === r.region_uid)) rows.push(r); });
  // regions with no production and nothing accrued go last (stable sort).
  rows.sort((a, b) => (a.status === 'nothing' && a.idle ? 1 : 0) - (b.status === 'nothing' && b.idle ? 1 : 0));
  return { fresh: true, rows };
}

// an empty region names its reason; the harvest cooldown with the time left.
function coolLeft(r) {
  const at = r && r.next_op_at ? Date.parse(r.next_op_at) : NaN;
  return Number.isFinite(at) && at > Date.now() ? (at - Date.now()) / 3.6e6 : 0;
}

function nothingText(r) {
  const h = coolLeft(r);
  return h ? `${r.reason || 'Harvest cooldown'} · next harvest in ${fmtDuration(h)}` : (r.reason || 'Nothing to harvest');
}

// a cooling region's resources exist -- show them (warning colour), never as 0.
function waitingHtml(r) {
  const w = r.waiting_net_by_token || {};
  const keys = sortedTokens(w).filter((t) => w[t] > 0);
  if (!keys.length || !coolLeft(r)) return '';
  return `<span class="hm-wait">${keys.map((t) => tok(t, w[t])).join('')}<span class="hm-wait-note">claimable in ${fmtDuration(coolLeft(r))}</span></span>`;
}

// the receipts panel retains its state dot; : the maintainer removed the
// redundant harvest-pill dots because each pill already states its status in glyph/text.
function statusPill(r) {
  if (r.status === 'included') return '<span class="hm-status is-ok">✓ Costs covered</span>';
  if (r.status === 'short') {
    const [sym, amt] = Object.entries(r.affordability?.short || {})[0] || [];
    return `<span class="hm-status is-bad">⚠ Short ${fmt2(amt)} ${esc(tokenLabel(sym).toLowerCase())}</span>`;
  }
  if (r.status === 'unknown') return '<span class="hm-status is-bad">Balance unavailable</span>';
  if (r.status === 'unavailable') return '<span class="hm-status is-bad">Quote unavailable</span>';
  if (r.status === 'nothing') return coolLeft(r) ? `<span class="hm-status is-warn">Cooldown · ${fmtDuration(coolLeft(r))}</span>` : `<span class="hm-status is-muted">${r.idle ? 'No production' : 'Nothing to harvest'}</span>`;
  return '<span class="hm-status is-muted"><span class="hm-skel is-pill" aria-hidden="true"></span><span class="hm-sr">Checking</span></span>';
}

function regionRowHtml(r, sharedKeys = null) {
  const picking = M.scopeKind === 'regions';
  const emptyIdle = r.idle && r.status === 'nothing' && ![...Object.values(r.net_by_token || {}), ...Object.values(r.waiting_net_by_token || {})].some(value => Number(value) > 0);
  const open = !picking && !emptyIdle && M.expanded.has(r.region_uid) && r.status !== 'checking';
  const net = r.net_by_token || {};
  const recs = r.status === 'checking' ? '<span class="hm-skel is-line" aria-hidden="true"></span>' : (Object.values(net).some(value => Number(value) > 0) ? `<span class="hm-resource-summary hm-resource-summary--compact">${harvestResourceColumns(net, r.cost_now, { sharedKeys })}</span>` : waitingHtml(r) || `<span class="hm-dim">${esc(nothingText(r))}</span>`);
  const name = r.name ? `<small>${esc(r.name)}</small>` : '';
  const territories = { PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' };
  const slug = territorySlug(territories[String(r.region_uid).split('-')[1]]);
  let body = '';
  if (open) {
    const costs = sortedTokens(r.cost_now).filter(symbol => Number(r.cost_now[symbol]) > 0);
    const balances = costs.map(symbol => {
      const have = r.balances?.[symbol];
      const affordability = affordabilityRow(r.affordability, symbol);
      const available = affordability?.available ?? have;
      const remaining = affordability?.after ?? null;
      const short = Number(affordability?.short || 0) > 0;
      return `<div class="hm-balance-item"><strong>${esc(tokenLabel(symbol))}</strong><span>${available == null ? 'Balance unavailable' : `Available ${fmt2(available)}`}</span><b class="${short ? 'hm-need' : 'hm-okc'}">${short ? `Short ${fmt2(affordability.short)} before harvest` : remaining == null ? '—' : `${fmt2(remaining)} after harvest`}</b></div>`;
    }).join('');
    body = `<div class="hm-rbody"><div class="hm-resource-summary">${balances ? `<section class="hm-balance-details"><h4>Regional balances after harvest</h4><div class="hm-balance-grid">${balances}</div></section>` : ''}</div>${r.reason ? `<p class="hm-hint">${esc(r.reason)}</p>` : ''}</div>`;

  }
  return `<div class="hm-region${r.status === 'short' || r.status === 'unknown' || r.status === 'unavailable' ? ' is-short' : ''}${r.status === 'nothing' && coolLeft(r) ? ' is-cool' : ''}${r.status === 'nothing' && r.idle ? ' is-idle' : ''}">${picking ? `<label class="hm-rhead is-pick"><input type="checkbox" class="hm-rcheck" value="${esc(r.region_uid)}" data-hm-region-check${M.selectedRegions.has(r.region_uid) ? ' checked' : ''}${r.status === 'nothing' ? ' disabled' : ''} aria-label="Harvest region ${esc(r.region_number)}">` : `<div class="hm-rhead${emptyIdle ? ' is-static' : ''}"${emptyIdle ? '' : ` data-hm="toggle-region" data-region="${esc(r.region_uid)}" role="button" tabindex="0" aria-expanded="${open}"`}><span${emptyIdle ? '' : ' class="hm-chev ceres-chevron"'} aria-hidden="true"></span>`}<span class="hm-rname"><span class="ch-plot-badge${slug ? ` territory-${slug}` : ''}">${regionTitle(r)}</span>${name}<small>${r.plots ?? '—'} plots</small></span><span class="hm-rrec" aria-label="Received after tax">${recs}</span>${statusPill(r)}${picking ? '</label>' : '</div>'}${body}</div>`;
}

// the Taxes tab -- every Keep and Castle, ticked one by one or per kind, collected in one batch.
const TAX_TERRAINS = new Set(['badlands', 'bog', 'caldera', 'canyon', 'desert', 'forest', 'hills', 'jungle', 'lake', 'mountain', 'plains', 'river', 'swamp', 'tundra']);

function taxPlots() {
  return (BOARD?.plots || []).filter((p) => ['keep', 'castle'].includes(String(p.worksite_type || '').toLowerCase()))
    .map((p) => ({ ...p, kind: String(p.worksite_type).toLowerCase() }))
    .sort((a, b) => String(a.region).localeCompare(String(b.region), undefined, { numeric: true }) || String(a.plot).localeCompare(String(b.plot), undefined, { numeric: true }));
}

// (maintainer picked option 2): the Keep/Castle picture of this plot's own terrain stands before its plot badge
// about the badge's height, in place of the "Keep · plot …" words that ran into the first resource icon.
function taxArt(p) {
  const terrain = String(p.terrain || '').toLowerCase();
  return `${ASSET_BASE}/land/worksites/productionBuildingUniform/tax/${p.kind}/${TAX_TERRAINS.has(terrain) ? terrain : 'plains'}_icon.webp?r=${ART_REV}`;
}

function taxHeld(deed) {
  return (plotTaxes(deed)?.tokens || []).filter((item) => Number(item.balance) > 0);
}

function chosenTaxDeeds() {
  return taxPlots().filter((p) => M.selectedTaxes.has(p.deed_uid) && taxHeld(p.deed_uid).length).map((p) => p.deed_uid);
}

// Reads each Keep/Castle's taxes once per open (the board's boxes usually have them already); a read that lands
// redraws, a failed one shows its error -- never a re-read loop ().
function askTaxes(plots) {
  plots.forEach((p) => {
    if (M.taxAsked.has(p.deed_uid) || plotTaxes(p.deed_uid)) return;
    M.taxAsked.add(p.deed_uid);
    const openId = M.openId;
    const redraw = () => { if (M.open && openId === M.openId && M.scopeKind === 'taxes' && !M.run) render(); };
    // A burst of 60 reads can meet a transient game error: one retry after 4 s, then the row keeps its error.
    refreshPlotTaxes(p.deed_uid, { force: true }).then(() => {
      redraw();
      if (plotTaxesError(p.deed_uid)) setTimeout(() => { if (M.open && openId === M.openId) refreshPlotTaxes(p.deed_uid, { force: true }).then(redraw); }, 4000);
    });
  });
}

function taxBodyHtml() {
  const plots = taxPlots();
  if (!plots.length) return '<div class="hm-statusline"><span>You have no Keeps or Castles.</span></div>';
  askTaxes(plots);
  if (!M.taxInit) { M.taxInit = true; plots.forEach((p) => M.selectedTaxes.add(p.deed_uid)); }
  const chosen = new Set(chosenTaxDeeds());
  const totals = {};
  chosen.forEach((d) => taxHeld(d).forEach((item) => { const k = String(item.token).toUpperCase(); totals[k] = (totals[k] || 0) + Number(item.balance); }));
  const reading = plots.filter((p) => !plotTaxes(p.deed_uid) && !plotTaxesError(p.deed_uid)).length;
  const line = reading ? `<div class="hm-statusline is-busy"><span class="hm-spin" aria-hidden="true"></span><span>Reading the collected taxes · ${plots.length - reading} of ${plots.length}…</span></div>`
    : chosen.size ? `<div class="hm-statusline is-ok"><span aria-hidden="true">✓</span><span>${chosen.size} ticked · a tax claim has no costs.</span></div>`
      : '<div class="hm-statusline"><span>Tick one or more Keeps or Castles to collect.</span></div>';
  const keys = sortedTokens(totals);
  const cards = keys.length ? keys.map((t) => { const icon = tokenIcon(t); return `<div class="hm-total">${icon ? `<img src="${icon}" alt="">` : ''}<span><b>${fmt2(totals[t])}</b><small>${esc(tokenLabel(t))}</small></span></div>`; }).join('')
    : '<p class="hm-dim">Nothing ticked holds taxes yet.</p>';
  // each button carries its building -- the Keep/Castle icon of the player's own first plot of that kind.
  const kindBtn = (kind, label) => {
    const deeds = plots.filter((p) => p.kind === kind && taxHeld(p.deed_uid).length).map((p) => p.deed_uid);
    const all = deeds.length && deeds.every((d) => M.selectedTaxes.has(d));
    const own = plots.find((p) => p.kind === kind && TAX_TERRAINS.has(String(p.terrain || '').toLowerCase()));
    const src = `${ASSET_BASE}/land/worksites/productionBuildingUniform/tax/${kind}/${own ? String(own.terrain).toLowerCase() : 'plains'}_icon.webp?r=${ART_REV}`;
    return `<button type="button" class="hm-btn is-secondary hm-kind-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="tax-kind" data-kind="${kind}"${deeds.length ? '' : ' disabled'}><img src="${src}" alt="" aria-hidden="true">${all ? 'Deselect' : 'Select'} all ${label}</button>`;
  };
  // Keeps/Castles holding taxes first; the empty ones follow, faded (stable sort keeps region/plot order).
  const ordered = plots.slice().sort((x, y) => (taxHeld(x.deed_uid).length ? 0 : 1) - (taxHeld(y.deed_uid).length ? 0 : 1));
  const rows = ordered.map((p) => {
    const t = plotTaxes(p.deed_uid);
    const error = plotTaxesError(p.deed_uid);
    const held = taxHeld(p.deed_uid);
    const cap = Number(t?.capacity) || 0;
    const pct = t && cap > 0 ? Math.min(100, (Number(t.total) / cap) * 100) : null;
    const recs = !t ? (error ? '<span class="hm-dim">Tax read unavailable</span>' : '<span class="hm-skel is-line" aria-hidden="true"></span>')
      : held.length ? held.map((item) => tok(String(item.token).toUpperCase(), Number(item.balance))).join('') : '<span class="hm-dim">Nothing collected yet</span>';
    const pill = pct == null ? (error ? '<span class="hm-status is-bad">Unavailable</span>' : '<span class="hm-status is-muted"><span class="hm-skel is-pill" aria-hidden="true"></span></span>')
      : `<span class="hm-status ${held.length ? 'is-ok' : 'is-muted'}">${fmt2(pct)}% full</span>`;
    const name = p.kind === 'castle' ? 'Castle' : 'Keep';
    const region = String(p.region || '').split('-').pop().padStart(3, '0');
    return `<div class="hm-region is-tax${held.length ? '' : ' is-idle'}"><label class="hm-rhead is-pick"><input type="checkbox" class="hm-rcheck" value="${esc(p.deed_uid)}" data-hm-tax-check${chosen.has(p.deed_uid) ? ' checked' : ''}${held.length ? '' : ' disabled'} aria-label="Collect taxes ${name} plot ${esc(formatPlotLabel(p.plot))}"><span class="hm-rname is-tax-name"><img class="hm-tax-art" src="${esc(taxArt(p))}" alt="${name}"><span class="hm-tax-id"><span class="ch-plot-badge ${territoryCellHtml(p.territory, p.deed_uid).cls}">${esc(formatPlotLabel(p.plot))}</span><small>Region ${esc(region)}${p.terrain ? ` · ${esc(p.terrain)}` : ''}</small></span></span><span class="hm-rrec">${recs}</span>${pill}</label></div>`;
  }).join('');
  return `${line}<div class="hm-resource-summary"><h3 class="hm-sec-h">You collect · ${chosen.size} Keep${chosen.size === 1 ? '' : 's'}/Castle${chosen.size === 1 ? '' : 's'}</h3><div class="hm-totals">${cards}</div><p class="hm-hint">Read fresh from the game. Totals are per resource, never summed across resources.</p></div><div><div class="hm-tax-kinds">${kindBtn('keep', 'Keeps')}${kindBtn('castle', 'Castles')}</div><div class="hm-regions">${rows}</div></div>`;
}


function routingEntryHtml(after = false) {
  if (M.v2Kinds.length) return `<div class="hr-entry"><div><h3>Route harvested resources</h3><p>${M.v2Kinds.includes('lp_wallet') ? 'Sell confirmed harvest for DEC or pair it with reserved wallet DEC in a pool.' : 'Sell a share of confirmed harvest for DEC.'}</p></div><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="routing">${after ? 'Route proceeds' : 'Set up routes'}</button></div>`;
  if (!HARVEST_ROUTING_ENABLED) return '';
  return `<div class="hr-entry"><div><h3>${after ? 'Route this harvest' : 'Harvest routing'}</h3><p>${after ? 'Move confirmed proceeds, sell a share or add liquidity.' : 'Cover input costs first, then decide where your harvest goes.'}</p></div><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="routing">${after ? 'Route proceeds' : 'Configure routing'}</button></div>`;
}

function openRouting() {
  if (M.v2Kinds.length) {
    if (M.mode !== 'batch' || M.scopeKind === 'taxes' || M.run?.busy) return;
    const account = M.player;
    const openId = M.openId;
    // (maintainer: routes did not open on a region harvest until a region was ticked): with no region ticked yet
    // routing opens for all regions -- routes are planned per region and apply to whatever is harvested.
    const scope = M.scopeKind === 'regions' && M.selectedRegions.size ? { kind: 'regions', region_uids: [...M.selectedRegions] } : { kind: 'all' };
    // (maintainer: regions that harvest nothing "must be excluded by default"): routes are offered only for regions that
    // produced a resource in this session's harvest; with no result data yet, every region stays offered.
    const produced = new Set((M.run?.members || []).filter((m) => m.region_uid && Object.entries(m.received_by_token || {})
      .some(([symbol, value]) => !['DEC', 'SPS', 'VOUCHER'].includes(symbol) && Number(value) > 0)).map((m) => m.region_uid));
    const producedKeys = new Set((M.run?.members || []).flatMap((m) => Object.entries(m.received_by_token || {})
      .filter(([symbol, value]) => !['DEC', 'SPS', 'VOUCHER'].includes(symbol) && Number(value) > 0).map(([symbol]) => `${m.region_uid}|${String(symbol).toUpperCase()}`)));
    const offered = produced.size ? boardRegions().filter((region) => produced.has(region.region_uid)) : boardRegions();
    const setup = createRoutingSetup({ player: account, scope, regions: offered, produced: producedKeys.size ? producedKeys : null, receipts: M.run?.attemptIds || [], initialRules: M.routeDrafts, onRulesChange: (rules) => { if (M.player === account) M.routeDrafts = rules; },
      enabledKinds: M.v2Kinds, offerOnlyKinds: M.v2OfferOnly, signingEnabled: M.v2Signing, onRender: render,
      isCurrent: () => M.v2 === setup && M.open && M.openId === openId && M.player === account });
    M.v2 = setup;
    render();
    setup.load();
    return;
  }
  if (!HARVEST_ROUTING_ENABLED) return;
  if (M.mode !== 'batch' || M.scopeKind === 'taxes' || M.run?.busy) return;
  const account = M.player;
  const openId = M.openId;
  const scope = M.scopeKind === 'regions' ? { kind: 'regions', region_uids: [...M.selectedRegions] } : { kind: 'all' };
  if (scope.kind === 'regions' && !scope.region_uids.length) { M.error = 'Choose one or more regions first.'; render(); return; }
  let routing;
  routing = createHarvestRouting({ player: account, scope, receipts: M.run?.attemptIds || [], activeRunId: M.routingRunId, onRender: render,
    isCurrent: () => M.routing === routing && M.open && M.openId === openId && M.player === account && (CURRENT_PLAYER || BOARD?.player) === account,
    onDone: closeHarvestModal,
    onBack: () => {
      M.routingRunId = routing.runId || M.routingRunId;
      M.routingTerminal = routing.terminal;
      routing.clear();
      M.routing = null;
      render();
      if (!M.run) loadPreview();
    },
    onHarvest: (run) => {
      const expectedRegions = [...(scope.region_uids || [])].sort().join(',');
      const savedRegions = [...(run.scope.region_uids || [])].sort().join(',');
      if (run.scope.kind !== scope.kind || (scope.kind === 'regions' && savedRegions !== expectedRegions)) {
        M.error = 'Routing failed: the current harvest selection does not match its routing plan.'; render(); return;
      }
      M.run = null; M.routingRunId = run.run_id; M.routingTerminal = false; M.harvestPreparing = true;
      M.scopeKind = run.scope.kind;
      M.selectedRegions = new Set(run.scope.region_uids || []);
      confirmHarvest();
    },
  });
  M.routing = routing;
  render();
  M.routing.load();
}

function batchStatusPill(region) {
  if (region?.status === 'included') return { label: 'Ready', tone: 'is-ok' };
  if (region?.status === 'short') return { label: 'Needs funding', tone: 'is-bad', blocking: true };
  if (region?.status === 'unknown') return { label: 'Balance unavailable', tone: 'is-bad', blocking: true };
  if (region?.status === 'unavailable') return { label: 'Quote unavailable', tone: 'is-bad', blocking: true };
  if (region?.status === 'nothing') return coolLeft(region) ? { label: `Cooldown · ${fmtDuration(coolLeft(region))}`, tone: 'is-warn', blocking: true } : { label: 'Nothing to harvest', tone: 'is-muted' };
  return { label: 'Preview pending', tone: 'is-muted' };
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
  const slots = (values, kind, resources, loading = false) => batchSlotsHtml(layout.resources, values, resources, kind, loading);
  const totalValues = (kind) => Object.fromEntries(layout.resources.map((symbol) => {
    const amount = totals[symbol] || { received: 0, cost: 0 };
    const net = netAfterCosts({ token: symbol, net: amount.received, costs: { [symbol]: amount.cost } })[0].amount;
    return [symbol, kind === 'received' ? amount.received : kind === 'cost' ? amount.cost : net];
  }));
  const totalOpen = M.costsOpen.has('total');
  const totalToggle = `<button type="button" class="hm-batch-costs ceres-btn ceres-btn--secondary" data-control-density="compact" data-hm="show-costs" aria-expanded="${totalOpen}"${fresh ? '' : ' disabled aria-hidden="true" tabindex="-1"'}>${totalOpen ? 'Hide costs' : 'Show costs'}</button>`;
  const idleRows = chosen.filter((row) => !row.resources.length);
  const noProduction = idleRows.length ? `<div class="hm-batch-no-production"><span>No production:</span>${idleRows.map((row) => {
    return batchRegionPlaqueHtml(row.region_number, row.region_uid);
  }).join('')}</div>` : '';
  const totalLines = totalOpen && fresh
    ? batchLedgerRows(slots(totalValues('received'), 'received', layout.resources), slots(totalValues('cost'), 'cost', layout.resources), slots(totalValues('net'), 'net', layout.resources))
    : `<div class="hm-batch-ledger-line"><span class="hm-batch-ledger-label" aria-hidden="true"></span>${slots(totalValues('net'), 'net', layout.resources, !fresh)}<span class="hm-batch-toggle-spacer" aria-hidden="true"></span></div>`;
  const regionLines = rows.map((row) => {
    const number = String(row.region_number);
    const safeNumber = esc(number);
    const open = M.costsOpen.has(row.region_uid);
    const plaque = batchRegionPlaqueHtml(number, row.region_uid);
    const picking = M.scopeKind === 'regions';
    // the label gives the tick box a 44 px tap area on the phone (display: contents on desktop).
    const pick = picking ? `<label class="hm-rcheck-hit"><input type="checkbox" class="hm-rcheck" value="${esc(row.region_uid)}" data-hm-region-check aria-label="Harvest region ${safeNumber}"${M.selectedRegions.has(row.region_uid) ? ' checked' : ''}${row.resources.length ? '' : ' disabled'}></label>` : '';
    if (!row.resources.length) return '';
    const idle = fresh && row.preview?.status === 'nothing';
    const received = {}, costs = {}, net = {};
    row.resources.forEach((symbol) => {
      received[symbol] = Number(row.preview?.net_by_token?.[symbol] || 0);
      costs[symbol] = Number(row.preview?.cost_now?.[symbol] || 0);
      net[symbol] = netAfterCosts({ token: symbol, net: received[symbol], costs: { [symbol]: costs[symbol] } })[0].amount;
    });
    const hasCosts = Object.values(costs).some((cost) => cost > 0);
    const toggle = `<button type="button" class="hm-batch-row-toggle" data-hm="show-costs" data-region="${esc(row.region_uid)}" aria-expanded="${open}" aria-label="${open ? 'Hide' : 'Show'} costs for region ${safeNumber}"${fresh && hasCosts ? '' : ' disabled'}><span class="ceres-chevron" aria-hidden="true"></span></button>`;
    const state = fresh ? batchStatusPill(row.preview || { status: 'nothing', idle: true }) : { label: 'Preview pending', tone: 'is-muted' };
    const stateColumn = `<span class="hm-batch-region-actions">${state.blocking && !idle ? `<span class="hm-batch-state-label ${state.tone}">${esc(state.label)}</span>` : ''}<span class="hm-batch-state-dot ${state.tone}" data-tip="${esc(state.label)}" aria-label="${esc(state.label)}"></span>${toggle}</span>`;
    const rowSlots = (kind, values) => slots(values, kind, row.resources, !fresh);
    const leading = picking ? `<span class="hm-batch-leading">${pick}${plaque}</span>` : plaque;
    const lines = idle
      ? `<div class="hm-batch-region-line">${leading}<span class="hm-batch-idle-status hm-batch-state-label ${state.tone}">${esc(state.label)}</span>${stateColumn}</div>`
      : batchRegionLedgerHtml({ leading, received: rowSlots('received', received), costs: rowSlots('cost', costs), net: rowSlots('net', net), actions: stateColumn, open: open && fresh });
    return `<div class="hm-batch-region${picking ? ' is-picking' : ''}" data-region="${esc(row.region_uid)}">${lines}</div>`;
  }).join('');
  return `${routingEntryHtml()}<div class="hm-batch-grid">${batchTotalHtml('Total · net after tax and costs', esc(status), totalToggle, totalLines)}<section class="hm-batch-regions"><h3 class="hm-sec-h">Regions</h3><div class="hm-batch-region-list">${regionLines}</div>${noProduction}</section></div>${(M.preview?.issues || []).length ? `<p class="hm-hint">${esc(M.preview.issues.join(' · '))}</p>` : ''}`;
}

// an empty batch says why instead of "0 Posting signatures".
function batchNothingText(rows) {
  const cool = rows.map(coolLeft).filter((h) => h > 0);
  return cool.length ? `Nothing to sign yet — harvest cooldown, next harvest in ${fmtDuration(Math.min(...cool))}` : 'Nothing to harvest right now';
}


// when the only
// thing stopping the confirm button is a missing wallet, it uses aria-disabled, not `disabled`, so its warning
// tooltip still shows on hover; handleAction ignores the click.
function confirmGateAttrs(blocked) {
  if (blocked) return ' disabled';
  if (walletReady(M.player)) return '';
  return ` aria-disabled="true"${tipAttrs({ title: 'Choose a wallet first', body: 'Pick Hive Keychain or HiveAuth with the Wallet button next to this one, then harvest.' }, 'caution')}`;
}

function footerHtml() {
  if (M.reviewing) {
    const label = M.scopeKind === 'taxes' ? 'Reviewing taxes…' : 'Reviewing harvest…';
    return `<footer class="hm-foot"><span class="hm-note" role="status" aria-live="polite"><span class="hm-spin" aria-hidden="true"></span>${label}</span><span class="hm-btns"><button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="close">Close</button></span></footer>`;
  }
  const pv = M.preview;
  const signingOn = !!(pv?.signing_enabled ?? M.state?.signing_enabled);
  if (M.mode === 'batch' && M.scopeKind === 'taxes') {
    const n = chosenTaxDeeds().length;
    const sigs = Math.ceil(n / 4);
    const note = n ? `${sigs} Posting signature${sigs === 1 ? '' : 's'} · ${n} tax claim${n === 1 ? '' : 's'} · no costs` : 'Nothing ticked';
    const primary = !signingOn
      ? `<button type="button" class="hm-btn is-primary is-off ceres-btn ceres-btn--primary" data-control-density="comfortable" disabled${pv ? ' aria-describedby="harvest-signing-note"' : ''}>${pv ? 'Signing unavailable' : 'Preparing harvest…'}</button>`
      : `<button type="button" class="hm-btn is-primary ceres-btn ceres-btn--primary" data-control-density="comfortable" data-hm="confirm"${confirmGateAttrs(!n || M.run)} data-control-case="commit">${COMMIT_ICON}Collect taxes</button>`;
    return `<footer class="hm-foot">${pv && !signingOn ? `<span class="hm-note" id="harvest-signing-note">${SIGNING_OFF_TEXT}</span>` : ''}<span class="hm-note">${M.error ? esc(M.error) : note}</span><span class="hm-btns"><button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="close">Close</button><span class="wallet-sign-pair">${walletControlsHtml(M.player, M.walletControlId, { compactRequest: true })}${primary}</span></span></footer>`;
  }
  const sigs = pv ? pv.signatures : null;
  const chosenRows = M.mode === 'batch' && M.scopeKind === 'regions' ? (pv?.regions || []).filter((r) => M.selectedRegions.has(r.region_uid)) : (pv?.regions || []);
  const funded = (pv?.regions || []).filter((r) => r.status === 'included').length;
  const chosenBlocked = chosenRows.some((r) => ['short', 'unknown', 'unavailable'].includes(r.status));
  const secondary = M.mode === 'batch' && M.scopeKind === 'all' && pv?.blocked && funded ? '<button type="button" class="hm-btn is-secondary ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="funded-only">Harvest funded regions only</button>' : '';
  const taxPlot = M.mode === 'plot' && String((BOARD?.plots.find((p) => p.deed_uid === M.deedUids[0]) || {}).resource || '').toUpperCase() === 'TAX';
  let label = M.mode === 'plot' ? (taxPlot ? 'Collect taxes' : 'Confirm harvest') : M.scopeKind === 'all' ? 'Harvest all' : 'Harvest regions';
  if (HARVEST_ROUTING_ENABLED && M.mode === 'batch' && M.scopeKind !== 'taxes' && !M.routingRunId) {
    const scope = M.scopeKind === 'regions' ? { kind: 'regions', region_uids: [...M.selectedRegions] } : { kind: 'all' };
    try { if (hasSavedHarvestRouting(M.player, scope)) label = 'Harvest + routes'; }
    catch (_) { label = 'Review saved routing'; }
  }
  const batchSigs = M.mode === 'batch' && pv ? Math.ceil(chosenRows.filter((r) => Object.keys(r.net_by_token || {}).length).length / 4) : null;
  // A blocked or empty review names why nothing can be signed instead of "0 signatures".
  const reasons = (pv?.regions || []).flatMap((r) => [r.reason, ...(r.deeds || []).map((d) => d.reason)]).filter(Boolean);
  const nothing = M.mode === 'plot' && pv && !pv.chunks?.length && !pv.blocked_regions?.length;
  const nextOp = reasons.find((r) => r.startsWith('Next harvest allowed at '));
  const nextText = nextOp ? `Nothing to sign yet — next harvest allowed in ${fmtDuration((Date.parse(nextOp.slice('Next harvest allowed at '.length)) - Date.now()) / 3.6e6)}` : null;
  const note = M.error ? esc(M.error) : M.loading ? 'Reading fresh costs and balances…'
    : nothing ? esc(nextText || reasons[0] || 'Nothing to harvest right now')
    : batchSigs === 0 && pv ? esc(batchNothingText(chosenRows))
    : batchSigs != null ? `${batchSigs} Posting signature${batchSigs === 1 ? '' : 's'} · costs calculated at harvest`
    : sigs != null ? `${sigs} Posting signature${sigs === 1 ? '' : 's'}${taxPlot ? ' · no costs' : ' · costs calculated at harvest'}` : (M.error ? esc(M.error) : '');
  const primary = !signingOn
    ? `<button type="button" class="hm-btn is-primary is-off ceres-btn ceres-btn--primary" data-control-density="comfortable" disabled${pv ? ' aria-describedby="harvest-signing-note"' : ''}>${pv ? 'Signing unavailable' : 'Preparing harvest…'}</button>`
    : `<button type="button" class="hm-btn is-primary ceres-btn ceres-btn--primary" data-control-density="comfortable" data-hm="confirm"${confirmGateAttrs(!pv || M.run || (M.mode === 'plot' ? (pv.blocked || !pv.chunks?.length) : (chosenBlocked || !batchSigs)))} data-control-case="commit">${COMMIT_ICON}${label}</button>`;
  return `<footer class="hm-foot">${pv && !signingOn ? `<span class="hm-note" id="harvest-signing-note">${SIGNING_OFF_TEXT}</span>` : ''}<span class="hm-note">${note}</span><span class="hm-btns"><button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="close">Close</button>${secondary}<span class="wallet-sign-pair">${walletControlsHtml(M.player, M.walletControlId, { compactRequest: true })}${primary}</span></span></footer>`;
}

const OUTCOME_TEXT = { queued: 'Signed — queued', success: 'Harvested', failed: 'Failed', pending: 'Confirmation pending', waiting: 'Waiting', not_sent: 'Not sent' };

function tokenCells(obj) {
  const keys = sortedTokens(obj || {}).filter((t) => obj[t] != null);
  return keys.length ? keys.map((t) => tok(t, obj[t])).join('') : '<span class="hm-dim">—</span>';
}

function runBodyHtml() {
  const run = M.run;
  if (run.phase === 'success') {
    return harvestSuccessHtml(run, { tax: isTaxCollection(), costsOpen: M.costsOpen, layout: M.regionLayout });
  }
  const banner = {
    keychain: '',
    queued: '<div class="hm-run is-busy" role="status"><span class="hm-spin" aria-hidden="true"></span><b>Signed — queued transactions are being submitted…</b></div>',
    confirming: `<div class="hm-run is-busy" role="status" aria-live="polite"><span class="hm-spin" aria-hidden="true"></span><span><b>Broadcast — confirming…</b> ${run.txId ? `Transaction ${esc(run.txId.slice(0, 12))}… is on its way; reading the game's result.` : ''}</span></div>`,
    failed: `<div class="hm-run is-bad" role="alert"><span class="hm-run-ic" aria-hidden="true">✕</span><span><b>Harvest failed.</b> ${esc(run.message || '')}</span></div>`,
    pending: `<div class="hm-run is-pending" role="status" aria-live="polite"><span class="hm-spin" aria-hidden="true"></span><span><b>${run.recoveryAttemptId ? 'Wallet outcome unknown.' : 'Transaction progress pending.'}</b> ${esc(run.message || 'The transaction is on its way; checking automatically.')}</span><button type="button" class="hm-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="check-now"${run.checking ? ' disabled aria-busy="true"' : ''}>${run.checking ? 'Checking…' : 'Check now'}</button></div>`,
    verification_incomplete: `<div class="hm-run is-pending" role="status" aria-live="polite"><span><b>${run.txId ? 'Harvest sent' : 'Harvest outcome unknown'} — verification incomplete.</b> ${esc(run.message || 'The game has not supplied a complete receipt yet.')}</span><button type="button" class="hm-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="check-now"${run.checking || !run.verifyIds?.size ? ' disabled' : ''}${run.checking ? ' aria-busy="true"' : ''}>${run.checking ? 'Checking…' : 'Check now'}</button></div>`,
  }[run.phase] || '';
  const rows = run.members.map((m) => {
    const where = m.deed_uids?.length ? `Plot ${esc(m.plotLabel || m.deed_uids[0])}` : `Region ${esc(m.region_uid.split('-').pop().padStart(3, '0'))}`;
    const outcome = m.outcome || (run.txId ? 'pending' : 'waiting');
    const finds = (m.finds || []).length ? m.finds.map((f) => esc(f.kind === 'fragment' ? `Totem fragment (${f.type || 'unknown'})` : "Labor's Luck card")).join(', ') : (outcome === 'success' ? 'None' : '—');
    return `<div class="hm-rgrid-row" role="row"><span role="cell" data-label="Scope">${where}</span><span role="cell" data-label="Result" class="hm-out is-${outcome}">${esc(OUTCOME_TEXT[outcome] || outcome)}${m.error ? `: ${esc(m.error)}` : ''}</span><span role="cell" data-label="Received · after tax">${tokenCells(m.received_by_token)}</span><span role="cell" data-label="Resources spent">${tokenCells(m.costs_by_token)}</span><span role="cell" data-label="Found">${finds}</span></div>`;
  }).join('');
  const table = `<div class="hm-rgrid" role="table" aria-label="Harvest result"><div class="hm-rgrid-row is-head" role="row"><span role="columnheader">Scope</span><span role="columnheader">Result</span><span role="columnheader">Received · after tax</span><span role="columnheader">Resources spent</span><span role="columnheader">Found</span></div>${rows}</div>`;
  // a harvest sent as several transactions lists every one, not only the last.
  const txIds = [...new Set([...(run.txIds || []), ...(run.txId ? [run.txId] : [])])];
  const tx = txIds.length ? `<p class="hm-hint">${txIds.length > 1 ? 'Transactions' : 'Transaction'} ${txIds.map((id) => `<a href="https://hivehub.dev/tx/${esc(id)}" target="_blank" rel="noopener">${esc(id)}</a>`).join(' · ')}</p>` : '';
  const stop = run.busy && run.chunks > 1 ? '<button type="button" class="hm-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="stop">Stop after this transaction</button>' : '';
  const recovery = run.recoveryAttemptId ? `<div class="hm-card"><label class="hr-field"><span>Harvest transaction ID from wallet history</span><input id="harvest-recovery-tx" autocomplete="off" spellcheck="false"></label><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-hm="recover-harvest"${run.busy ? ' disabled' : ''}>Recover harvest</button></div>` : '';
  return `${banner}${recovery}<div class="hm-card">${table}${tx}${stop}</div>`;
}

function resultFooterHtml() {
  if (M.mode !== 'batch' || M.scopeKind === 'taxes') {
    return '<footer class="hm-foot"><span class="hm-note"></span><span class="hm-btns"><button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="close">Close</button></span></footer>';
  }
  const routing = M.v2Kinds.length || HARVEST_ROUTING_ENABLED ? '<button type="button" class="hm-btn is-primary ceres-btn ceres-btn--primary" data-control-density="comfortable" data-result-action="routing" data-hm="routing">Routing</button>' : '';
  return `<footer class="hm-foot"><span class="hm-note"></span><span class="hm-btns">${routing}<button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-result-action="close" data-hm="close">Close</button></span></footer>`;
}

function modalBodyHtml() {
  if (M.v2) return M.v2.html();
  if (M.routing && !M.harvestPreparing) return M.routing.html();
  if (M.harvestPreparing && !M.run) {
    const status = M.error
      ? `<section class="hm-run is-bad" role="alert"><b>Harvest review failed.</b> ${esc(M.error)}<p>The routing plan remains saved. Refresh the harvest state before trying again.</p></section>`
      : '<section class="hm-run is-busy" role="status" aria-live="polite"><b>Refreshing harvest eligibility…</b></section>';
    return `${status}${M.routing?.html() || ''}`;
  }
  if (M.run) return runBodyHtml() + (!M.run.busy && M.run.phase !== 'success' && M.mode === 'batch' && M.scopeKind !== 'taxes' && (M.routingRunId || M.run.attemptIds?.length) ? routingEntryHtml(true) : '');
  return `${scopeHtml()}${M.mode === 'plot' ? plotBodyHtml() : M.scopeKind === 'taxes' ? taxBodyHtml() : batchBodyHtml()}`;
}

function renderInner() {
  const el = document.getElementById('harvest-modal-overlay');
  if (!el || !M.open) return;
  const panel = el.querySelector('.harvest-panel');
  panel.classList.toggle('is-batch', M.mode === 'batch' && !M.run && !M.v2);
  panel.classList.toggle('is-r3', !!M.v2);
  const body = modalBodyHtml();
  if (M.v2?.record && patchRoutingWindow(panel.querySelector('.r3-window'), body)) { scrollRouteIndexToActive(panel); updateRouteScrollCue(panel); drawRoutePointers(panel); return; }
  const focusedKey = document.activeElement?.dataset?.hm ? `[data-hm="${document.activeElement.dataset.hm}"]` : null;
  const focusedRegion = document.activeElement?.dataset?.hm === 'show-costs' ? document.activeElement.dataset.region : null;
  const scroll = panel.querySelector('.hm-body')?.scrollTop || 0;
  const routeListScroll = panel.querySelector('.r3-setup .r3-route-index')?.scrollTop || 0;
  // the wallet chooser no longer sits in its own row above the body -- footerHtml()
  // places it directly left of the Sign button, and the routing bundle action (harvest-routing.js)
  // places it left of its own Sign button while M.routing is active.
  const runFooter = M.run?.busy ? `<footer class="hm-foot"><span class="hm-note${M.run.phase === 'keychain' && walletKind(M.player) === 'hiveauth' ? ' is-action' : ''}" role="status" aria-live="polite">${M.run.phase === 'keychain' ? `<span class="hm-spin" aria-hidden="true"></span>${esc(walletSigningStatus(M.player))}${walletSigningStatus(M.player).endsWith('.') ? '' : '…'}` : ''}</span><span class="hm-btns"><span class="wallet-sign-pair">${walletControlsHtml(M.player, M.walletControlId, { compactRequest: true })}</span></span></footer>` : (M.run?.phase === 'success' ? resultFooterHtml() : '<footer class="hm-foot"><span class="hm-note"></span><span class="hm-btns"><button type="button" class="hm-btn is-ghost ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="close">Close</button></span></footer>');
  const v2Footer = '<footer class="hm-foot"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="routing-back">Back to harvest</button><span class="hm-btns r3-modal-actions"></span></footer>';
  panel.innerHTML = `<header class="hm-headr"><div>${titleHtml()}</div><button type="button" class="harvest-x ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-hm="close" aria-label="Close">${X_ICON}</button></header><div class="hm-body">${body}</div>${M.v2 ? v2Footer : M.routing ? M.routing.footerHtml() : M.run ? runFooter : footerHtml()}`;
  const v2Actions = panel.querySelector('.r3-actions');
  if (M.v2 && v2Actions) panel.querySelector('.r3-modal-actions')?.replaceChildren(...v2Actions.content.cloneNode(true).childNodes);
  syncHarvestResourceDividers(panel);
  observeHarvestResourceDividers(panel);
  panel.querySelectorAll('.harvest-routing select, .r3-setup select').forEach(select => {
    const field = select.dataset.hrField || select.dataset.hrReserve || select.dataset.r3Field;
    initCustomSelect(select, {
      ariaLabel: select.closest('label')?.querySelector('span')?.textContent || 'Routing option',
      decorateOption: ['symbol', 'destination_symbol'].includes(field)
        ? value => tokenIcon(value) ? { icon: `<img class="hr-resource-icon" src="${esc(tokenIcon(value))}" alt="">` } : null
        // the wallet's region plaque.
        : field === 'source_region' ? (value) => ({ icon: regionPlaque(value) }) : undefined,
    });
    if (select.closest('.r3-setup')) select._csel?.trigger.setAttribute('data-control-density', 'comfortable');
  });
  const newBody = panel.querySelector('.hm-body');
  if (newBody) newBody.scrollTop = scroll;
  // only the route list scrolls, and the selected route
  // stays in view so its pointer reaches the edit panel.
  const routeList = panel.querySelector('.r3-setup .r3-route-index');
  if (routeList) {
    routeList.scrollTop = routeListScroll;
    const chip = routeList.querySelector('.r3-route-chip.is-selected');
    if (chip) {
      const pad = parseFloat(getComputedStyle(routeList).paddingBlockStart) || 0;
      if (chip.offsetTop - pad < routeList.scrollTop) routeList.scrollTop = chip.offsetTop - pad;
      else if (chip.offsetTop + chip.offsetHeight + pad > routeList.scrollTop + routeList.clientHeight) routeList.scrollTop = chip.offsetTop + chip.offsetHeight + pad - routeList.clientHeight;
    }
  }
  if (focusedKey) {
    const target = focusedRegion ? [...panel.querySelectorAll('[data-hm="show-costs"]')].find((button) => button.dataset.region === focusedRegion) : panel.querySelector(focusedKey);
    target?.focus?.();
  }
  layoutOpenWalletDrawers(panel);
  scrollRouteIndexToActive(panel);
  updateRouteScrollCue(panel);
  drawRoutePointers(panel);
  // a shown cooldown counts down -- one redraw a minute, only while a cooldown is on screen and nothing runs.
  clearTimeout(M.cooldownTimer);
  if (panel.querySelector('.hm-batch-state-label.is-warn, .hm-status.is-warn')) {
    M.cooldownTimer = setTimeout(() => { if (M.open && !M.run?.busy && !M.v2) render(); }, 60000);
  }
}

function render() {
  try { renderInner(); }
  catch (error) {
    if (!M.run || !M.open) throw error;
    const run = M.run;
    run.phase = 'verification_incomplete';
    run.busy = false;
    run.renderFailed = true;
    run.message = 'The receipt could not be displayed. Check the transaction or try verification again.';
    const panel = document.getElementById('harvest-modal-overlay')?.querySelector('.harvest-panel');
    if (!panel) return;
    const tx = run.txId ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(run.txId)}" target="_blank" rel="noopener noreferrer">View transaction</a>` : '';
    panel.innerHTML = `<header class="hm-headr"><h2 id="harvest-modal-title">${run.txId ? 'Harvest sent' : 'Harvest outcome unknown'} — verification incomplete</h2><button type="button" class="harvest-x ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-mode="icon" data-hm="close" aria-label="Close">${X_ICON}</button></header><div class="hm-body"><p role="status">${esc(run.message)}</p>${tx}</div><footer class="hm-foot"><span class="hm-btns"><button type="button" class="hm-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="check-now"${run.verifyIds?.size ? '' : ' disabled'}>Check now</button><button type="button" class="hm-btn ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-hm="close">Close</button></span></footer>`;
  }
}

function onTick(now) {
  if (!M.open || M.mode !== 'plot' || M.run) return;
  const data = plotHarvest(M.deedUids[0]);
  const p = data?.projection;
  if (!p) return; // nothing ticks until the fresh read has painted
  const a = advanceProjection(p, now);
  if (!a.known) return;
  const el = document.getElementById('harvest-modal-overlay');
  const set = (k, v) => { el.querySelectorAll(`[data-hl="${k}"]`).forEach(n => { if (n.textContent !== v) n.textContent = v; }); };
  const netRow = netAfterCosts({ token: p.token, net: a.net, costs: a.costs })[0];
  set('net', netRow?.amount == null ? '—' : `${netRow.amount < 0 ? '−' : '+'}${fmt2(Math.abs(netRow.amount))}`); set('net2', fmt2(a.net)); set('gross', fmt2(a.gross)); set('tax', `−${fmt2(a.tax)}`);
  el.querySelector('.hm-plot-ledger-row.is-gain')?.classList.toggle('is-loss', netRow?.amount < 0);
  if (a.fragment != null) set('frag', fmtPct(a.fragment));
  if (a.laborsLuck != null) set('ll', fmtPct(a.laborsLuck));
  const bal = data?.region_balances;
  Object.entries(a.costs).forEach(([s, v]) => {
    set(`cost-${s}`, fmt2(v));
    const cell = el.querySelector(`[data-hl="after-${s}"]`);
    if (cell && bal && bal[s] != null && v != null) {
      const perHour = (p.costs || []).filter(item => item.symbol === s).reduce((sum, item) => sum + Number(item.per_hour || 0), 0);
      const available = bal[s] + (s === p.token && !p.is_sps ? a.net : 0);
      const need = v + perHour * Number(data?.affordability?.margin_hours || 0);
      const short = Math.max(0, need - available);
      const text = short > 0 ? `Short ${fmt2(short)} before harvest` : fmt2(available - need);
      if (cell.textContent !== text) { cell.textContent = text; cell.className = short ? 'hm-need' : 'hm-okc'; }
    }
  });
  const cap = el.querySelector('[data-hl="cap"]');
  if (cap) {
    cap.style.setProperty('--pct', `${a.pct.toFixed(2)}%`);
    cap.dataset.level = capacityLevel(a.pct);
    set('cap-pct', `${fmt2(a.pct)}% full`);
    set('cap-rest', a.full ? '· production paused · holds 1 week of production' : `· full in ${fmtDuration(a.hoursToFull)} · holds 1 week of production`);
  }
}

// signing (reachable only when the server's harvest_signing_enabled is on)

async function postJson(url, body) {
  const resp = await fetch(playerQueryAppend(url), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) { const error = new Error(data.detail || `HTTP ${resp.status}`); error.status = resp.status; throw error; }
  return data;
}

function saveHarvestAttempt(attemptId, fields = {}) {
  return postJson('/api/harvest/attempt', { attempt_id: attemptId, run_id: M.run?.runId || null, ...fields });
}

// (live incident, 2026-09-29): a 5-region harvest (two transactions, one of 990 plots) was accepted by the
// game but ended "verification incomplete": the game can take ~30 s to index a harvest and a busy server needed more
// than 10 s per check, so every check was early or cut off before the 45 s deadline. Checks now run for 2 minutes and
// each may take 30 s; "Check now" stays available afterwards.
const VERIFY_SCHEDULE_S = [0, 5, 12, 25, 45, 75, 120];
const VERIFY_MAX_WAIT_MS = 150000;
const VERIFY_REQUEST_MS = 30000;

// Polls the verifier until the game's verdict is final (). A transaction id never
// reads as "not sent"; pending resolves itself on the schedule, and "Check now" re-reads.
function harvestMemberConfirmed(member) {
  return member.outcome === 'success' && member.game_state === 'accepted' && member.evidence?.length;
}

function harvestMembersTerminal(members) {
  return members?.length && members.every((member) => harvestMemberConfirmed(member) || (member.outcome === 'failed' && member.game_state === 'refused'));
}

function harvestAmountsUnreconcilable(members) {
  return members?.some((member) => member.game_state === 'accepted' && member.receipt?.gaps?.includes('game_amounts_unavailable'));
}

function applyHarvestVerification(run, result, attemptId) {
  if (!Array.isArray(result?.members)) return;
  result.members.forEach((member) => {
    const target = run.members.find((row) => row.nonce === member.nonce);
    if (target) Object.assign(target, member);
  });
  if (result.members.some(harvestMemberConfirmed) && !run.attemptIds.includes(attemptId)) { run.attemptIds.push(attemptId); balancesChanged({ player: M.player, assets: ['DEC', 'SPS', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'], regions: [...new Set(result.members.map(m => m.region_uid).filter(Boolean))], id: `harvest:${attemptId}` }); }
}

function boundedHarvestVerify(attemptId) {
  let timer;
  return Promise.race([
    postJson('/api/harvest/verify', { attempt_id: attemptId }).catch(() => null),
    new Promise((resolve) => { timer = setTimeout(() => resolve(null), VERIFY_REQUEST_MS); }),
  ]).finally(() => clearTimeout(timer));
}

async function checkHarvestNow(run) {
  if (run.checking || !run.verifyIds?.size) return;
  run.checking = true;
  render();
  try {
    const results = await Promise.all([...run.verifyIds].map(async (id) => ({ id, result: await boundedHarvestVerify(id) })));
    if (M.run !== run || !M.open) return;
    results.forEach(({ id, result }) => applyHarvestVerification(run, result, id));
    if (harvestMembersTerminal(run.members)) {
      run.phase = run.members.every(harvestMemberConfirmed) ? 'success' : 'failed';
      run.renderFailed = false;
    } else {
      run.phase = 'verification_incomplete';
      run.message = 'The game has not supplied a complete receipt. You can check again later.';
    }
  } finally {
    run.checking = false;
    if (M.run === run && M.open) render();
  }
}

function verifyUntilFinal(attemptId, chunkMembers) {
  return new Promise((resolve) => {
    const run = M.run;
    run.verifyIds ||= new Set();
    run.verifyIds.add(attemptId);
    run.pollers ||= new Map();
    let settled = false;
    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(deadlineTimer);
      run.pollers.delete(attemptId);
      if (outcome === 'pending') {
        run.checking = false;
        run.phase = 'verification_incomplete';
        run.message = 'The game has not supplied a complete receipt. Nothing will be sent again; check the transaction or try verification later.';
        render();
      }
      resolve(outcome);
    };
    let step = 0;
    let timer = null;
    const deadlineTimer = setTimeout(() => finish('pending'), VERIFY_MAX_WAIT_MS);
    const once = async () => {
      if (settled) return;
      clearTimeout(timer);
      const result = await boundedHarvestVerify(attemptId);
      if (settled || M.run !== run || !M.open) return;
      run.checking = false; // the manual re-check has its answer
      applyHarvestVerification(run, result, attemptId);
      if (harvestAmountsUnreconcilable(result?.members)) { finish('pending'); return; }
      if (harvestMembersTerminal(result?.members)) {
        finish(result.members.every(harvestMemberConfirmed) ? 'success' : 'failed'); return;
      }
      if (!M.routingRunId && ['success', 'failed'].includes(result?.outcome)) { finish(result.outcome); return; }
      step += 1;
      if (step >= 3 && run.phase === 'confirming') { run.phase = 'pending'; }
      render();
      if (run.renderFailed) { finish('pending'); return; }
      if (step < VERIFY_SCHEDULE_S.length) timer = setTimeout(once, (VERIFY_SCHEDULE_S[step] - VERIFY_SCHEDULE_S[step - 1]) * 1000);
      else finish('pending');
    };
    run.pollers.set(attemptId, once);
    run.pollNow = () => { if (run.checking) return; if (run.phase === 'verification_incomplete') { checkHarvestNow(run); return; } run.checking = true; render(); run.pollers.forEach((poll) => poll()); };
    once();
  });
}

async function recoverHarvest() {
  if (!M.run?.recoveryAttemptId || !M.routingRunId || M.run.busy) return;
  const run = M.run;
  const tx = document.getElementById('harvest-recovery-tx')?.value.trim();
  run.busy = true;
  render();
  try {
    await postJson('/api/harvest/routing/harvest-transaction', {
      run_id: M.routingRunId, attempt_id: run.recoveryAttemptId, tx_id: tx,
    });
    if (M.run !== run || !M.open) return;
    M.run = null;
    await confirmHarvest();
  } catch (error) {
    if (M.run === run) { run.message = error.message || String(error); run.busy = false; render(); }
  }
}

async function confirmHarvest() {
  if (!M.open || M.run || M.reviewing) return;
  const account = M.player;
  const wallet = walletKind(account);
  const openId = M.openId;
  const gen = M.gen;
  const mode = M.mode;
  const scopeKind = M.scopeKind;
  const selection = () => JSON.stringify(scopeKind === 'taxes' ? chosenTaxDeeds().sort() : [...M.selectedRegions].sort());
  const selectionKey = selection();
  const taxDeeds = mode === 'batch' && scopeKind === 'taxes' ? chosenTaxDeeds() : [];
  const current = () => M.open && M.openId === openId && M.player === account && (CURRENT_PLAYER || BOARD?.player || null) === account;
  const token = {};
  let pv = M.preview;
  let savedHarvestAttempts = [];
  M.reviewing = token;
  M.error = null;
  render();
  try {
    if (!walletReady(account)) { M.error = 'Wallet connection unavailable. Reconnect the selected wallet.'; return; }
    if (M.routingRunId) {
      const saved = await postJson('/api/harvest/routing/harvest-plan', { run_id: M.routingRunId });
      pv = saved.preview; savedHarvestAttempts = saved.attempts;
    } else if (mode === 'batch' && scopeKind === 'regions') {
      pv = await postJson('/api/harvest/preview', { scope: { kind: 'regions', region_uids: [...M.selectedRegions] } });
    } else if (mode === 'batch' && scopeKind === 'taxes') {
      if (!taxDeeds.length) { M.error = 'No selected Keeps or Castles have taxes to collect.'; return; }
      pv = await postJson('/api/harvest/preview', { scope: { kind: 'taxes', deed_uids: taxDeeds } });
    }
    if (!current()) return;
    if (M.gen !== gen || M.mode !== mode || M.scopeKind !== scopeKind || selection() !== selectionKey) {
      M.error = 'The selection changed during review. Confirm the current selection again.';
      return;
    }
    if (!pv) { M.error = 'Harvest review unavailable. Refresh the review.'; return; }
    if (!pv.signing_enabled) { M.error = SIGNING_OFF_TEXT; return; }
    if (pv.blocked) { M.error = (pv.issues || []).join(' · ') || 'The fresh review is blocked. Resolve the listed issues before signing.'; return; }
    if (!pv.chunks?.length) { M.error = (pv.issues || []).join(' · ') || (scopeKind === 'taxes' ? 'Nothing to collect right now.' : 'Nothing to harvest right now.'); return; }
    if (walletKind(account) !== wallet) { M.error = 'The selected wallet changed during review. Confirm again with the current wallet.'; return; }
    if (!walletReady(account)) { M.error = 'Wallet connection expired during review. Reconnect the selected wallet.'; return; }
  } catch (error) {
    if (current()) M.error = `Fresh ${scopeKind === 'taxes' ? 'tax' : 'harvest'} review unavailable: ${error.message || error}`;
    return;
  } finally {
    if (M.reviewing === token) {
      M.reviewing = null;
      if (M.open && M.openId === openId) {
        if (current()) render(); else closeHarvestModal();
      }
    }
  }
  if (!current() || M.run) return;
  clearStaleWalletRequest(account);
  if (M.harvestPreparing) {
    M.routing?.clear?.();
    M.routing = null;
    M.harvestPreparing = false;
  }
  const plotLabel = (deed) => { const row = BOARD?.plots.find((p) => p.deed_uid === deed); return row ? formatPlotLabel(row.plot) : deed; };
  M.run = { busy: true, stop: false, phase: 'keychain', txId: null, message: '', chunks: pv.chunks.length,
    runId: M.routingRunId ? routingClientRunId(account, M.routingRunId) : mintHarvestRunId(),
    attemptIds: [], members: pv.chunks.flatMap((c) => c.members.map((m) => ({ ...m, plotLabel: m.deed_uids?.[0] ? plotLabel(m.deed_uids[0]) : null, outcome: null }))) };
  render();
  let finalOutcome = 'success';
  const prepared = new Map();
  const submitted = [];
  const handedOff = new Set();
  try {
    for (let index = 0; index < pv.chunks.length; index += 1) {
      const saved = savedHarvestAttempts.find((item) => item.chunk_index === index);
      if (saved && saved.evidence?.state !== 'rejected_before_send') continue;
      const attemptId = saved?.attempt_id || crypto.randomUUID().replace(/-/g, '').slice(0, 24);
      const flight = await postJson('/api/harvest/preflight', { preview_id: pv.preview_id, preview_digest: pv.preview_digest, chunk_index: index });
      if (flight.outcome !== 'PERMIT') throw new Error(flight.reason || 'Harvest is unavailable');
      prepared.set(index, { attemptId, flight });
    }
    const signingEnvelopes = await prepareSigningBatch(account, [...prepared.values()].map(item => item.flight.chunk.operations), 'Posting', { runId: M.run.runId, runKind: 'harvest' });
    [...prepared.values()].forEach((item, index) => { item.envelope = signingEnvelopes?.[index]; if (item.envelope) item.envelope.context = { harvestAttemptId: item.attemptId }; });
    if (prepared.size) {
      await postJson('/api/harvest/prepare', { preview_id: pv.preview_id, preview_digest: pv.preview_digest,
        attempts: [...prepared].map(([chunk_index, item]) => ({ chunk_index, attempt_id: item.attemptId })) });
    }
    if (!current() || walletKind(account) !== wallet || !walletReady(account)) throw new Error('The player or wallet changed before signing');
    for (let index = 0; index < pv.chunks.length; index += 1) {
      if (M.run.stop) { finalOutcome = 'pending'; break; }
      const savedAttempt = savedHarvestAttempts.find((item) => item.chunk_index === index);
      const attemptId = savedAttempt?.attempt_id || prepared.get(index).attemptId;
      if (savedAttempt && savedAttempt.evidence?.state !== 'rejected_before_send') {
        const evidence = savedAttempt.evidence;
        if (!evidence?.tx_id) {
          M.run.phase = 'pending';
          M.run.message = 'A saved harvest wallet request has no transaction ID. Recover its result before continuing; nothing will be sent again.';
          M.run.recoveryAttemptId = attemptId;
          finalOutcome = 'pending';
          break;
        }
        M.run.txId = evidence.tx_id; (M.run.txIds ||= []).push(M.run.txId);
        (evidence.members || []).forEach((member) => { const target = M.run.members.find((row) => row.nonce === member.nonce); if (target) Object.assign(target, member); });
        const outcome = harvestMembersTerminal(evidence.members)
          ? (evidence.members.every(harvestMemberConfirmed) ? 'success' : 'failed')
          : await verifyUntilFinal(attemptId);
        const members = M.run.members.filter((member) => pv.chunks[index].members.some((expected) => expected.nonce === member.nonce));
        if (harvestMembersTerminal(members)) {
          if (members.some(harvestMemberConfirmed) && !M.run.attemptIds.includes(attemptId)) { M.run.attemptIds.push(attemptId); balancesChanged({ player: M.player, assets: ['DEC', 'SPS', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'], regions: [...new Set(members.map(m => m.region_uid).filter(Boolean))], id: `harvest:${attemptId}` }); }
          if (outcome !== 'success') finalOutcome = 'failed';
          continue;
        }
        finalOutcome = outcome; M.run.phase = outcome; break;
      }
      const { flight, envelope } = prepared.get(index);
      let submission = null;
      M.run.phase = 'keychain';
      render();
      let txId = null;
      const chunkNonces = new Set(flight.chunk.members.map((m) => m.nonce));
      try {
        handedOff.add(attemptId);
        const result = await requestSignatureThroughGate(account, flight.chunk.operations, (response) => {
          const id = response?.result?.id || response?.result?.tx_id || response?.tx_id;
          if (response?.success && id) {
            txId = id; M.run.txId = id; (M.run.txIds ||= []).push(M.run.txId); M.run.phase = 'confirming'; render();
            saveHarvestAttempt(attemptId, { tx_id: id, state: 'submitted' }).catch(() => {});
          }
        }, 'Posting', envelope);
        txId = result.txId;
        submission = result.submitted;
        if (result.queued) {
          M.run.members.forEach(m => { if (chunkNonces.has(m.nonce)) m.outcome = 'queued'; });
          saveHarvestAttempt(attemptId, { tx_id: txId, state: 'signed_queued' }).catch(() => {});
        }
      } catch (error) {
        txId = txId || error.txId || null;
        if (error.broadcasted === false) {
          M.run.phase = 'failed';
          M.run.message = error.message || 'The wallet request failed. This batch was not accepted.';
          M.run.members.forEach((m) => { if (chunkNonces.has(m.nonce)) m.outcome = 'not_sent'; });
          await saveHarvestAttempt(attemptId, { state: 'rejected_before_send' }).catch(() => {});
          finalOutcome = 'failed';
          break;
        }
        if (!txId) {
          M.run.phase = 'pending';
          M.run.message = 'The wallet did not return a transaction id. Nothing will be sent again; check your wallet history.';
          finalOutcome = 'pending';
          break;
        }
        submitted.push({ attemptId, txId, submission });
        finalOutcome = 'pending';
        M.run.message = 'The wallet response needs verification. Remaining transactions were not sent.';
        break;
      }
      if (M.run.phase === 'keychain') M.run.phase = 'queued';
      render();
      submitted.push({ attemptId, txId, submission });
    }
  } catch (error) {
    finalOutcome = M.run.txId ? 'pending' : 'failed';
    M.run.phase = finalOutcome;
    M.run.message = M.run.txId ? `Could not read the result yet (${error.message || error}). Nothing will be sent again.` : `${error.message || error} — nothing was sent.`;
  }
  for (const { attemptId, flight } of prepared.values()) {
    if (!handedOff.has(attemptId)) {
      const nonces = new Set(flight.chunk.members.map((member) => member.nonce));
      M.run.members.forEach((member) => { if (nonces.has(member.nonce)) member.outcome = 'not_sent'; });
      await saveHarvestAttempt(attemptId, { state: 'rejected_before_send' }).catch(() => {});
    }
  }
  try {
    if (submitted.length) {
      M.run.phase = 'queued'; render();
      const outcomes = await Promise.all(submitted.map(async ({ attemptId, txId, submission }) => {
        try { if (submission) await submission; }
        catch (error) {
          M.run.message = error.message;
          if (error.broadcasted === false) {
            await saveHarvestAttempt(attemptId, { state: 'failed' }).catch(() => {});
            return 'failed';
          }
          return 'pending';
        }
        M.run.txId = txId; M.run.phase = 'confirming'; (M.run.txIds ||= []).push(M.run.txId); render();
        await saveHarvestAttempt(attemptId, { tx_id: txId, state: 'submitted' }).catch(() => {});
        return verifyUntilFinal(attemptId);
      }));
      if (outcomes.includes('failed')) finalOutcome = 'failed';
      else if (outcomes.includes('pending') && finalOutcome !== 'failed') finalOutcome = 'pending';
    }
    if (M.run.renderFailed) finalOutcome = 'pending';
    M.run.phase = finalOutcome === 'pending' ? 'verification_incomplete' : finalOutcome;
    M.run.busy = false;
    if (finalOutcome === 'failed') {
      M.run.phase = 'failed';
      const refusals = M.run.members.filter(member => member.outcome === 'failed' && member.error).map(member => member.error);
      M.run.message = M.run.message || (refusals.length ? refusals.join(' · ') : 'The game refused this harvest. The routing plan remains saved; refresh the harvest state before trying again.');
      if (!M.run.message.includes('routing plan')) M.run.message += ' The routing plan remains saved.';
    }
    if (finalOutcome === 'success') {
      M.costsOpen = new Set();
      M.run.phase = 'success';
      const recv = M.run.members.flatMap((m) => sortedTokens(m.received_by_token || {}).map((t) => `${fmt2(m.received_by_token[t])} ${tokenLabel(t).toLowerCase()}`));
      M.run.message = recv.length ? `You received ${recv.join(', ')}.` : 'Confirmed by the game.';
      M.deedUids.forEach((deed) => refreshPlotHarvest(deed, { force: true }));
      taxDeeds.forEach((deed) => refreshPlotTaxes(deed, { force: true }));
    }
    render();
  } catch (error) {
    if (M.run && M.open) {
      M.run.phase = 'verification_incomplete';
      M.run.busy = false;
      M.run.message = 'The receipt could not be completed. Check the transaction or try verification later.';
      render();
    }
  }
}

window.addEventListener('ceres:wallet-changed', () => { if (M.open) render(); });
