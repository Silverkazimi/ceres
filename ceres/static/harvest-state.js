// harvest -- shared client state: live accrual math, one 1-second ticker, formatting
// and the plot harvest bar (approved v3, variant V3-2, plan §22). No network I/O inside the tick.
import { ART_REV } from './art.js';
import { CURRENT_PLAYER, playerQueryAppend } from './board.js';
import { esc, humanizeInstant } from './format.js';
import { plotPoweredAfterStage } from './hazard.js';
import { POWER_CORE_IMAGE, resourceIconHtml, tooltipIconPath } from './icons.js';
import { netAfterCosts } from './harvest-net.js';
import { ASSET_BASE } from './state.js';
import { tipAttrs } from './tooltip.js';
import { formatCountdown } from './countdown.js';
import { resourceFieldMap } from './resources.js';

const RESOURCE_LABELS = resourceFieldMap('label');

export const CAP_HOURS = 168;
const FRESH_AFTER_MS = 60000;
const PLOTS = new Map(); // `${player}|${deed}` -> {data, fetchedAt, inFlight}
const SUBSCRIBERS = new Set();
let TIMER = null;
let SKEW_MS = 0; // server_now - client now

// every amount rounds to two decimals.
export function fmt2(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtPct(fraction) {
  if (fraction == null || !Number.isFinite(fraction)) return '—';
  return `${fmt2(fraction * 100)}%`;
}

// warn 70-99%, danger at 100% (actually capped), ok below.
export function capacityLevel(pct) {
  if (pct >= 100) return 'danger';
  if (pct >= 70) return 'warn';
  return 'ok';
}

export function tokenLabel(token) {
  const raw = String(token || '');
  const key = raw.toLowerCase();
  const known = RESOURCE_LABELS; // registry: dec DEC, grain Grain, wood Wood, stone Stone, iron Iron, aura Aura, sps SPS, research Research, voucher Voucher
  return known[key] || raw;
}

export function tokenIcon(token) {
  const path = tooltipIconPath('resource', String(token || '').toLowerCase());
  return path ? `${ASSET_BASE}/${path}` : '';
}

// centre the harvest box by what is visible, not by boxes -- resource icons carry uneven transparent
// padding (grain.png: 66/470 on the left), which widened the left gap by up to 4.5px. Each icon's empty left
// edge is measured once from its alpha and the icon is pulled left by exactly that much.
const INK_LEFT = new Map(); // icon url -> transparent left fraction
function inkShiftStyle(url) {
  const frac = INK_LEFT.get(url);
  return frac ? ` style="margin-left:calc(${(-frac).toFixed(4)} * 2rem)"` : '';
}
function measureInkLeft(image) {
  const url = image.getAttribute('src');
  if (!url || INK_LEFT.has(url) || !image.naturalWidth) return;
  try {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(image, 0, 0);
    const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    let left = width;
    for (let y = 0; y < height; y += 1) for (let x = 0; x < left; x += 1) if (data[(y * width + x) * 4 + 3] > 40) { left = x; break; }
    INK_LEFT.set(url, left < width ? left / width : 0);
  } catch (_) { INK_LEFT.set(url, 0); }
  const shift = inkShiftStyle(url);
  if (!shift) return;
  document.querySelectorAll(`.harvest-bar-res[src="${CSS.escape(url)}"]`).forEach((img) => { img.style.marginLeft = `calc(${(-INK_LEFT.get(url)).toFixed(4)} * 2rem)`; });
}
if (typeof document !== 'undefined') {
  document.addEventListener('load', (event) => { if (event.target?.classList?.contains('harvest-bar-res')) measureInkLeft(event.target); }, true);
}

export function noteServerNow(iso) {
  const at = Date.parse(iso || '');
  if (Number.isFinite(at)) SKEW_MS = at - Date.now();
}

export function serverNowMs() {
  return Date.now() + SKEW_MS;
}

// The same formula as ceres/harvest.py advance(): NET anchor + gross slope x (1 - tax) x hours
// gained while under the 168 h cap. Costs advance only while the plot is under the cap.
export function advanceProjection(p, nowMs = serverNowMs()) {
  if (!p || p.net_at_anchor == null || !p.anchor_at) return { known: false };
  const anchor = Date.parse(p.anchor_at);
  if (!Number.isFinite(anchor)) return { known: false };
  const h0 = Number(p.hours_at_anchor) || 0;
  const h = h0 + Math.max(0, nowMs - anchor) / 3.6e6;
  const hc = Math.min(h, CAP_HOURS);
  const dh = Math.max(0, hc - Math.min(h0, CAP_HOURS));
  const tax = Number(p.tax_rate ?? 0.1);
  const net = Number(p.net_at_anchor) + Number(p.gross_per_hour || 0) * (1 - tax) * dh;
  const gross = tax < 1 ? net / (1 - tax) : net;
  const costs = {};
  const groupedCosts = new Map();
  (p.costs || []).forEach((c) => {
    const prior = groupedCosts.get(c.symbol);
    groupedCosts.set(c.symbol, { accrued: prior ? prior.accrued : c.accrued, perHour: (prior?.perHour || 0) + Number(c.per_hour || 0) });
  });
  groupedCosts.forEach((c, symbol) => {
    costs[symbol] = c.accrued == null ? null : Number(c.accrued) + (h0 < CAP_HOURS ? c.perHour * dh : 0);
  });
  const finds = p.finds || {};
  // the fill is time since the last action -- but a plot producing nothing stores nothing, so its bar is
  // empty (it showed full on an empty plot with 0.00 to receive).
  const producing = Number(p.gross_per_hour || 0) > 0 || Number(p.net_at_anchor || 0) > 0;
  return {
    known: true, hours: h, cappedHours: hc, net, gross, tax: gross - net, costs, producing,
    pct: producing ? Math.min(100, (h / CAP_HOURS) * 100) : 0, full: producing && h >= CAP_HOURS,
    hoursToFull: Math.max(0, CAP_HOURS - h),
    fragment: finds.fragment_rate_per_hour != null ? Math.min(1, finds.fragment_rate_per_hour * hc) : null,
    laborsLuck: finds.labors_luck_rate_per_hour != null ? Math.min(1, finds.labors_luck_rate_per_hour * hc) : null,
  };
}

export function fmtDuration(hours) {
  if (!Number.isFinite(hours)) return '—';
  const total = Math.max(0, Math.round(hours * 60));
  const d = Math.floor(total / 1440);
  const h = Math.floor((total % 1440) / 60);
  const m = total % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function key(deedUid) {
  return `${CURRENT_PLAYER || ''}|${deedUid}`;
}

export function plotHarvest(deedUid) {
  return PLOTS.get(key(deedUid))?.data || null;
}

export function currentProjection(row) {
  return plotHarvest(row.deed_uid)?.projection || row.harvest || null;
}

// One narrow fresh read per plot per minute (server: /land/projects/deed/{deed}/active +
// one shared balances read, both cached 10 s); never the board bundle.
export async function refreshPlotHarvest(deedUid, { force = false } = {}) {
  const k = key(deedUid);
  const entry = PLOTS.get(k) || {};
  if (entry.inFlight) {
    if (!force) return entry.inFlight;
    await entry.inFlight;
    if (entry.inFlight) return entry.inFlight;
  }
  if (!force && entry.fetchedAt && Date.now() - entry.fetchedAt < FRESH_AFTER_MS) return entry.data;
  const player = CURRENT_PLAYER;
  entry.inFlight = (async () => {
    let fetched = false;
    try {
      let url = playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}/harvest`);
      if (force) url += `${url.includes('?') ? '&' : '?'}fresh=1`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (player !== CURRENT_PLAYER) return null;
      noteServerNow(data.server_now);
      entry.data = data;
      entry.error = null;
      fetched = true;
      // Paint the fresh value at once rather than on the next tick ().
      document.querySelectorAll(`.harvest-bar[data-deed-uid="${CSS.escape(deedUid)}"]`).forEach((bar) => patchSlot(bar, serverNowMs()));
    } catch (error) {
      entry.error = String(error);
      // never leave the box blank -- show a dash with the reason and retry in 20 s.
      document.querySelectorAll(`.harvest-bar[data-deed-uid="${CSS.escape(deedUid)}"]`).forEach((bar) => markUnavailable(bar));
      setTimeout(() => {
        if (document.querySelector(`.harvest-bar[data-deed-uid="${CSS.escape(deedUid)}"]`)) refreshPlotHarvest(deedUid, { force: true });
      }, 20000);
    } finally {
      entry.fetchedAt = Date.now();
      entry.inFlight = null;
    }
    ensureTicker();
    return fetched ? entry.data : null;
  })();
  PLOTS.set(k, entry);
  return entry.inFlight;
}

// (maintainer chose "One line + full list on hover"): a Keep/Castle's box shows the tax it has collected
// many resources at once. One read per plot per minute; the box is re-drawn when the read lands.
const TAXES = new Map(); // `${player}|${deed}` -> {data, fetchedAt, inFlight, error}
const TAX_ROWS = new Map(); // deed -> {row, cog}
export function plotTaxes(deedUid) {
  return TAXES.get(key(deedUid))?.data || null;
}
export function plotTaxesError(deedUid) {
  return TAXES.get(key(deedUid))?.error || null;
}
export async function refreshPlotTaxes(deedUid, { force = false } = {}) {
  const k = key(deedUid);
  const entry = TAXES.get(k) || {};
  if (entry.inFlight) return entry.inFlight;
  if (!force && entry.fetchedAt && Date.now() - entry.fetchedAt < FRESH_AFTER_MS) return entry.data;
  const player = CURRENT_PLAYER;
  entry.inFlight = (async () => {
    try {
      const resp = await fetch(playerQueryAppend(`/api/plots/${encodeURIComponent(deedUid)}/taxes`));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (player !== CURRENT_PLAYER) return null;
      entry.data = data;
      entry.error = null;
    } catch (error) {
      entry.error = String(error);
      setTimeout(() => { if (document.querySelector(`.harvest-bar.is-tax[data-tax-deed-uid="${CSS.escape(deedUid)}"]`)) refreshPlotTaxes(deedUid, { force: true }); }, 20000);
    } finally {
      entry.fetchedAt = Date.now();
      entry.inFlight = null;
    }
    redrawTaxBars(deedUid);
    return entry.data;
  })();
  TAXES.set(k, entry);
  return entry.inFlight;
}
// The read can land before the plot has put its box on the page (a fast answer): redraw now, and again shortly
// after for a box that is still showing its empty loading state.
function redrawTaxBars(deedUid, attempt = 0) {
  const held = TAX_ROWS.get(deedUid);
  if (!held) return;
  const bars = [...document.querySelectorAll(`.harvest-bar.is-tax[data-tax-deed-uid="${CSS.escape(deedUid)}"]`)];
  bars.forEach((bar) => { bar.outerHTML = taxBarHtml(held.row, held.cog, { noFetch: true }); });
  if (!bars.length && attempt < 3) setTimeout(() => redrawTaxBars(deedUid, attempt + 1), [300, 1000, 2500][attempt]);
}

function taxBarHtml(row, cogHtml, { noFetch = false } = {}) {
  TAX_ROWS.set(row.deed_uid, { row, cog: cogHtml });
  const entry = TAXES.get(key(row.deed_uid)) || {};
  const t = entry.data;
  if (!noFetch) setTimeout(() => refreshPlotTaxes(row.deed_uid), 0);
  ensureTicker();
  const type = String(row.worksite_type || '').toLowerCase();
  // S10 / L4-01: the terrain comes from the API; only a plain terrain word may reach the image address (it is also escaped).
  const rawTerrain = String(row.terrain || '').toLowerCase();
  const terrain = /^[a-z][a-z_-]{0,31}$/.test(rawTerrain) ? rawTerrain : '';
  const building = (type === 'keep' || type === 'castle') && terrain
    ? `<img class="harvest-bar-res" src="${ASSET_BASE}/land/worksites/productionBuildingUniform/tax/${esc(type)}/${esc(terrain)}_icon.webp?r=${ART_REV}" alt="${esc(type)}">` : '';
  const total = t ? Number(t.total) || 0 : null;
  const capacity = t ? Number(t.capacity) || 0 : 0;
  const pct = total !== null && capacity > 0 ? Math.min(100, (total / capacity) * 100) : 0;
  const shown = (t?.tokens || []).filter((item) => Number(item.balance) > 0);
  const items = shown.map((item) => ({ icon: tooltipIconPath('resource', String(item.token).toLowerCase()), name: tokenLabel(item.token), effect: fmt2(item.balance), group: 'Collected' }));
  const name = type === 'keep' ? 'Keep' : 'Castle';
  const tip = total === null
    ? (entry.error ? { title: 'Tax read unavailable', body: 'Retrying in 20 s.', peer: 'plot_warnings' } : null)
    : shown.length
      ? { title: `Tax collected ${fmt2(total)}`, items, foot: `${fmt2(pct)}% of this ${name.toLowerCase()}'s ${fmt2(capacity).replace(/\.00$/, '')} capacity.`, peer: 'candidate_metric' }
      : { title: 'Nothing collected yet', body: `This ${name.toLowerCase()} holds no taxes right now.`, peer: 'candidate_metric' };
  // (maintainer: "is this simply calculating all resources to one number together? ... change that. Focus on
  // capacity and maybe resources icons only"): the box shows how full the storage is plus an icon per resource held
  // the per-resource amounts stay in the tooltip. No sum across resources.
  const amount = total === null ? (entry.error ? '—' : '') : shown.length ? `${fmt2(pct)}%` : '0%';
  const label = total !== null && !shown.length ? 'Nothing collected yet' : 'Tax storage';
  const icons = shown.map((item) => { const src = tokenIcon(item.token); return src ? `<img class="harvest-bar-tax-ic" src="${src}" alt="${esc(tokenLabel(item.token))}">` : ''; }).join('');
  const amtCh = Math.max(6, (amount || '').length + 0.5, label.length * 0.62);
  return `<div class="harvest-bar is-tax${total === null && !entry.error ? ' is-pending' : ''}" data-tax-deed-uid="${esc(row.deed_uid)}" data-level="ok" data-reveal-group="controls" style="--pct:${pct.toFixed(2)}%">${cogHtml}<span class="harvest-bar-mid" style="--amt-ch:${amtCh.toFixed(1)}ch"${tip ? tipAttrs(tip) : ''}>${building}<span class="harvest-bar-stack"><span class="harvest-bar-lbl">${esc(label)}</span><span class="harvest-bar-amt">${esc(amount)}${icons ? `<span class="harvest-bar-tax-icons">${icons}</span>` : ''}</span></span></span><button type="button" data-control-density="header" data-control-mode="icon" class="ceres-btn ceres-btn--primary harvest-bar-btn harvest-bar-go" data-harvest-action="open-plot" data-deed-uid="${esc(row.deed_uid)}" aria-label="Collect taxes"${tipAttrs({ title: 'Collect taxes' })}>${SICKLE_SVG}</button><span class="harvest-bar-edge" role="meter" aria-label="Tax storage ${fmt2(pct)}% full" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100"><i></i></span></div>`;
}

export function subscribeTick(fn) {
  SUBSCRIBERS.add(fn);
  ensureTicker();
  return () => SUBSCRIBERS.delete(fn);
}

// a tax box that still shows no figure heals itself on the next tick -- it draws the data it now has, or
// starts the read if none is running (covers a read that landed before the box was on the page).
function healTaxBars() {
  document.querySelectorAll('.harvest-bar.is-tax[data-tax-deed-uid]').forEach((bar) => {
    const deed = bar.dataset.taxDeedUid;
    const entry = TAXES.get(key(deed)) || {};
    const empty = !bar.querySelector('.harvest-bar-amt')?.textContent;
    if (!empty) return;
    if (entry.data) redrawTaxBars(deed);
    else if (!entry.inFlight && !entry.error) refreshPlotTaxes(deed, { force: true });
  });
}

function tick() {
  if (document.hidden) return;
  healTaxBars();
  const now = serverNowMs();
  const chips = document.querySelectorAll('.harvest-bar[data-deed-uid]');
  chips.forEach((chip) => { if (chip.classList.contains('is-building') && !plotHarvest(chip.dataset.deedUid)) tickBuildBar(chip, now); else patchSlot(chip, now); });
  SUBSCRIBERS.forEach((fn) => { try { fn(now); } catch (error) { console.warn('harvest tick', error); } });
  if (!chips.length && !SUBSCRIBERS.size && !document.querySelector('.harvest-bar.is-tax')) { clearInterval(TIMER); TIMER = null; }
}

export function ensureTicker() {
  if (TIMER) return;
  TIMER = setInterval(tick, 1000);
}

function markUnavailable(bar) {
  if (!bar.classList.contains('is-pending')) return;
  const amt = bar.querySelector('[data-hb-amt]');
  if (amt) amt.textContent = '—';
  bar.querySelector('.harvest-bar-mid')?.setAttribute('data-tip', 'Live harvest read unavailable — retrying');
}

function patchSlot(bar, now) {
  const deed = bar.dataset.deedUid;
  const data = plotHarvest(deed);
  const projection = data?.projection || null;
  if (!projection) return;
  // the fresh read decides whether the box is a build countdown or a harvest amount. A box painted
  // from the capture's stale projection (old resource + amount) is redrawn as soon as the game says
  // "construction"; a build box is redrawn as a harvest box once the game says the plot produces again.
  const building = projection.state === 'construction';
  const buildBar = bar.classList.contains('is-building');
  // a build box already on the page is also redrawn the moment the fresh read reports the
  // build finished (feed-the-workers), not only when the state leaves "construction" entirely.
  const readyToFeed = building && !!projection.worksite_completion;
  const shownReady = bar.classList.contains('is-build-ready');
  if (building ? (!buildBar || (readyToFeed && !shownReady)) : (buildBar && ['harvesting', 'idle'].includes(projection.state))) {
    const kept = BAR_ROWS.get(deed);
    if (kept) { bar.outerHTML = harvestBarHtml(kept.row, { cog: kept.cog }); return; }
  }
  if (buildBar) { tickBuildBar(bar, now); return; }
  const a = advanceProjection(projection, now);
  if (!a.known) return;
  // the first number painted is the fresh one; after that it never steps back unless
  // the plot was harvested (a new last_action_time).
  const anchorKey = projection.last_action_time || '';
  let shown = netAfterCosts({ token: projection.token, net: a.net, costs: a.costs })[0]?.amount;
  if (!a.costs?.[projection.token] && bar.dataset.anchor === anchorKey && Number(bar.dataset.shown) > shown) shown = Number(bar.dataset.shown);
  bar.dataset.anchor = anchorKey;
  bar.dataset.shown = String(shown);
  bar.classList.remove('is-pending');
  const text = fmt2(shown);
  const amt = bar.querySelector('[data-hb-amt]');
  if (amt && amt.firstChild?.nodeValue !== text) { if (amt.firstChild) amt.firstChild.nodeValue = text; else amt.textContent = text; }
  const pct = `${a.pct.toFixed(2)}%`;
  if (bar.style.getPropertyValue('--pct') !== pct) bar.style.setProperty('--pct', pct);
  bar.classList.toggle('has-cap', a.pct > 0); // any accrual shows a visible stub
  const level = capacityLevel(a.pct);
  if (bar.dataset.level !== level) bar.dataset.level = level;
  const edge = bar.querySelector('.harvest-bar-edge');
  if (edge) { edge.setAttribute('aria-valuenow', a.pct.toFixed(0)); edge.setAttribute('aria-label', `Worksite capacity ${fmt2(a.pct)}% full`); }
  bar.querySelector('.harvest-bar-go')?.classList.toggle('is-short', data?.affordability?.status === 'short');
}

const COG_SVG = '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" fill-rule="evenodd" d="M10.1 4.9 10.2 2.1 13.8 2.1 13.9 4.9 17.2 6.8 19.7 5.5 21.5 8.6 19.1 10.1 19.1 13.9 21.5 15.4 19.7 18.5 17.2 17.2 13.9 19.1 13.8 21.9 10.2 21.9 10.1 19.1 6.8 17.2 4.3 18.5 2.5 15.4 4.9 13.9 4.9 10.1 2.5 8.6 4.3 5.5 6.8 6.8zM15.5 12a3.5 3.5 0 1 0-7.0 0a3.5 3.5 0 1 0 7.0 0z"/><path class="d" d="M17.3 13.9A5.6 5.6 0 0 1 9.2 16.8"/><circle class="a" cx="12" cy="12" r="2.1"/></svg>';
// the crafted header sickle (), two-tone via the .hdr-ic token classes.
export const SICKLE_SVG = '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M9 15.8C5.4 11.6 7 4.7 13.5 3.3c4.7-1 8 2.2 7.2 6.1-1.4-2.4-4-3.3-6.5-2.5-3.6 1.1-5 5-3.5 8.3z"/><path class="d" d="M10.9 13.6c-.9-2.9.7-6.2 3.6-7.1"/><path class="a h" d="M9.4 15.3 3.6 21.1"/></svg>';

const STATE_TEXT = { tax_collection: 'Tax collection is separate', construction: 'Under construction', no_project: 'No worksite yet — Build one now', /* fits the harvest box */ unavailable: 'Harvest data unavailable' };
const BAR_ROWS = new Map(); // deed -> {row, cog} (: a state change redraws the box from the row it was drawn from)
// a finished build is completed by feeding the workers Grain (the existing worksite modal's
// completion state), never by reloading the board.
const BUILD_DONE_TEXT = 'Build finished — feed the workers';

// the plot's worksite is being built or changed. The fresh read is the authority once it has landed
// before that the row's pending_change (the in-flight change badge's data) or the capture's construction state.
function buildInfo(row, fresh) {
  const fp = fresh?.projection || null;
  const pcAny = row?.pending_change || null;
  // the game flags every uncleared plot is_construction too (no worksite, nothing started). Only a
  // build with a start (a pending change, stalled or not, or a construction record with a start date) is
  // "Building"; an uncleared plot falls through to the no-worksite state.
  const started = !!(pcAny || fp?.construction?.started_at || fp?.construction?.to || row?.harvest?.construction?.started_at || row?.harvest?.construction?.to);
  if (fp && !['construction', 'unavailable'].includes(fp.state)) return null;
  let c = null;
  if (pcAny) c = pcAny;
  else if (fp) {
    if (fp.state === 'construction') c = fp.construction || {}; // the server applies the same start rule
    else if (fp.state === 'unavailable' && row?.is_construction && started) c = {};
    else return null;
  } else if ((row?.is_construction || row?.harvest?.state === 'construction') && started) c = row?.harvest?.construction || {};
  else return null;
  const to = c.to || pcAny?.to || null;
  const toLabel = c.to_label || pcAny?.to_label || (to ? String(to).toUpperCase() : '') || 'new worksite';
  // the fresh per-plot read decides completion once it has landed (it is the live source
  // see ceres/harvest.py's project_harvest_plot); before that, the board's own capture already carries
  // it when the page loaded AFTER the build finished (ceres/board.py's ready_worksite_change).
  const completion = fp ? (fp.source === 'fresh' ? (fp.worksite_completion || null) : null) : (row?.worksite_completion || null);
  return {
    to,
    toLabel,
    projectId: c.project_id ?? c.id ?? pcAny?.project_id ?? fp?.construction?.project_id ?? null,
    readyAt: c.ready_at || c.projected_end || pcAny?.ready_at || pcAny?.projected_end || null,
    progressRatio: c.progress_pct ?? pcAny?.progress_pct ?? null,
    startedAt: c.started_at || pcAny?.started_at || null,
    ppRequired: c.pp_required ?? pcAny?.pp_required ?? null,
    ppSpent: c.pp_spent ?? pcAny?.pp_spent ?? null,
    stalled: Boolean(c.stalled ?? pcAny?.stalled),
    completion,
  };
}

function buildLineHtml(info, now = serverNowMs()) {
  if (info.stalled) return `<span class="harvest-bar-amt harvest-bar-build-eta"><span>stalled</span></span>`;
  const readyAt = info.readyAt;
  const until = readyAt ? Date.parse(readyAt) : NaN;
  if (!Number.isFinite(until)) return `<span class="harvest-bar-amt harvest-bar-build-eta"><span>ready time unknown</span></span>`;
  if (until <= now) return `<span class="harvest-bar-amt harvest-bar-build-eta is-done">${BUILD_DONE_TEXT}</span>`;
  return `<span class="harvest-bar-amt harvest-bar-build-eta"><span>ready in <span data-countdown-until="${esc(readyAt)}">${formatCountdown(until - now)}</span></span></span>`;
}

function buildBarHtml(row, cogHtml, info) {
  const completion = info.completion || null;
  const actionButton = completion || info.projectId == null
    ? `<button type="button" data-control-density="header" data-control-mode="icon" class="ceres-btn ceres-btn--primary harvest-bar-btn harvest-bar-go" data-harvest-action="feed-workers" data-deed-uid="${esc(row.deed_uid)}" aria-label="Feed workers"${tipAttrs({ title: 'Feed the workers' })}>${SICKLE_SVG}</button>`
    : `<button type="button" data-control-density="header" data-control-mode="icon" class="ceres-btn ceres-btn--primary harvest-bar-btn harvest-bar-go" data-slot-action="cancel-construction" data-deed-uid="${esc(row.deed_uid)}" aria-label="Cancel construction"${tipAttrs({ title: 'Cancel construction' })}><svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`;
  // a finished build is fed, not reloaded -- the box switches to an active Feed-workers
  // action (same button classes as Harvest/Collect taxes) that opens the existing worksite modal's
  // completion state (worksite.js openWorksiteModal / requestWorksiteCompletionPreview).
  if (completion) {
    const grainKnown = completion.grain_cost != null;
    const grainText = grainKnown ? `${fmt2(completion.grain_cost)} Grain` : 'Grain';
    const title = `Feed the workers${grainKnown ? ` ${grainText}` : ''} to finish ${info.toLabel}`;
    return `<div class="harvest-bar is-building is-build-ready" data-deed-uid="${esc(row.deed_uid)}" data-level="ok" data-reveal-group="controls" data-build-until="${esc(info.readyAt || '')}">${cogHtml}<span class="harvest-bar-mid"${tipAttrs({ title })}><span class="harvest-bar-build-ic" aria-hidden="true"><span class="spinner"></span></span><span class="harvest-bar-stack"><span class="harvest-bar-lbl">Build finished</span><span class="harvest-bar-amt harvest-bar-build-eta is-done">Feed the workers</span></span></span>${actionButton}</div>`;
  }
  const when = info.readyAt ? humanizeInstant(info.readyAt) : null;
  const title = when?.absolute ? `Nothing to harvest until the build finishes (ready ${when.absolute})` : 'Nothing to harvest until the build finishes';
  const required = Number(info.ppRequired);
  const spent = Number(info.ppSpent);
  const reported = info.progressRatio == null ? NaN : Number(info.progressRatio);
  const start = info.startedAt ? Date.parse(info.startedAt) : NaN;
  const end = info.readyAt ? Date.parse(info.readyAt) : NaN;
  const elapsed = !info.stalled && Number.isFinite(start) && Number.isFinite(end) && end > start
    ? (serverNowMs() - start) / (end - start) : NaN;
  const ratio = !info.stalled && Number.isFinite(reported) ? reported
    : Number.isFinite(elapsed) ? elapsed
      : Number.isFinite(required) && required > 0 && Number.isFinite(spent) ? spent / required : 0;
  const progress = Math.max(0, Math.min(100, ratio * 100));
  const icon = info.to ? resourceIconHtml(info.to, info.toLabel) : '';
  return `<div class="harvest-bar is-building" data-deed-uid="${esc(row.deed_uid)}" data-level="ok" data-reveal-group="controls" data-build-until="${esc(info.readyAt || '')}" style="--pct:${progress.toFixed(2)}%">${cogHtml}<span class="harvest-bar-mid"${tipAttrs({ title })}><span class="harvest-bar-build-ic" aria-hidden="true"><span class="spinner"></span></span><span class="harvest-bar-stack"><span class="harvest-bar-lbl harvest-bar-build-name">Under construction — ${esc(info.toLabel)}${icon}</span>${buildLineHtml(info)}</span></span>${actionButton}<span class="harvest-bar-edge" role="meter" aria-label="Construction progress ${progress.toFixed(2)}%" aria-valuenow="${progress.toFixed(0)}" aria-valuemin="0" aria-valuemax="100"><i></i></span></div>`;
}

// Live countdown on a build box; at zero the line turns into the feed-the-workers hint, and the
// live per-plot facts are fetched once (/: never draw the finished state from the
// countdown alone) so patchSlot can redraw the box as soon as the game confirms completion.
function tickBuildBar(bar, now) {
  const node = bar.querySelector('[data-countdown-until]');
  if (!node) return;
  const until = Date.parse(node.dataset.countdownUntil);
  if (!Number.isFinite(until)) return;
  if (until <= now) {
    const eta = bar.querySelector('.harvest-bar-build-eta');
    if (eta) { eta.textContent = BUILD_DONE_TEXT; eta.classList.add('is-done'); }
    const button = bar.querySelector('.harvest-bar-go');
    if (button && button.dataset.slotAction !== 'cancel-construction') {
      button.disabled = false;
      button.removeAttribute('aria-disabled');
      button.dataset.harvestAction = 'feed-workers';
      button.setAttribute('aria-label', 'Feed workers');
      button.setAttribute('data-tip', 'Feed the workers');
    }
    if (!bar.querySelector('.harvest-bar-go[data-slot-action="cancel-construction"]')) bar.querySelector('.harvest-bar-mid')?.setAttribute('data-tip', 'Feed the workers');
    const deed = bar.dataset.deedUid;
    if (deed && !bar.dataset.completionCheck) { bar.dataset.completionCheck = '1'; refreshPlotHarvest(deed, { force: true }); }
    return;
  }
  const text = formatCountdown(until - now);
  if (node.textContent !== text) node.textContent = text;
}

// Approved harvest bar v3, variant V3-2 (/§22): cog LEFT (change worksite, replaces the
// old "Change worksite" button), resource icon + "You receive" + two-decimal net in the middle
// sickle Harvest RIGHT; only as wide as its content, centred under the worksite. The bottom
// edge is capacity (ok < 70%, warn 70-99%, danger at 100%); a red dot on the sickle when short.
export function harvestBarHtml(row, { cog = false } = {}) {
  const worksiteName = String(row.worksite_type || '').trim();
  const cogLabel = worksiteName ? `Change ${worksiteName}` : 'Choose worksite';
  const cogHtml = cog ? `<button type="button" data-control-density="header" data-control-mode="icon" class="ceres-btn ceres-btn--primary harvest-bar-btn harvest-bar-cog" data-slot-action="open-worksite-modal" data-deed-uid="${esc(row.deed_uid)}" aria-label="${esc(cogLabel)}"${tipAttrs({ title: cogLabel })}>${COG_SVG}</button>` : '';
  const fresh = plotHarvest(row.deed_uid);
  BAR_ROWS.set(row.deed_uid, { row, cog });
  // a worksite under construction (a 24 h change included) shows the build countdown, never the
  // old resource and amount; the per-plot game read (below) confirms or overturns it on landing.
  const build = buildInfo(row, fresh);
  if (build) { if (row.harvest || !fresh) setTimeout(() => refreshPlotHarvest(row.deed_uid), 0); ensureTicker(); return buildBarHtml(row, cogHtml, build); }
  // the board snapshot's anchor is stale after any harvest since the capture, so it only
  // decides layout (token icon, reserved width); every number comes from the fresh plot read.
  // the plot detail's projection is live from THIS page load's capture ( fixed the
  // merge that carried a days-old worksite record), so it is painted at once instead of an empty
  // box; the per-plot game read then refines it on the same anchor and patchSlot never steps back.
  const p = fresh?.projection || row.harvest;
  const pending = !p;
  if (row.harvest) setTimeout(() => refreshPlotHarvest(row.deed_uid), 0);
  if (p?.state === 'tax_collection' || String(row.resource || '').toUpperCase() === 'TAX') return taxBarHtml(row, cogHtml);
  const a = p && ['harvesting', 'idle'].includes(p.state) ? advanceProjection(p) : { known: false };
  if (!a.known) {
    // an uncleared plot with a staged build says so, instead of "No worksite yet".
    const stagedBuild = p?.state === 'no_project' ? globalThis.ceresStagedWorksiteBuild?.(row.deed_uid) : null; // accessor, no import
    const text = stagedBuild ? `Staged: ${stagedBuild.targetLabel || 'worksite'}` // the "signs with ..." tail removed
      : STATE_TEXT[p?.state] || (p ? STATE_TEXT.unavailable : '');
    // (his words: "a button next to build button, using the power core image ...
    // It will unlock the build button. Because otherwise it is a redundant pathway until a
    // power core is added"): an unpowered, not-yet-cleared plot gets a Power Core quick-stage
    // button next to Build. It reuses the EXACT click path the Power tile's own empty slot
    // already uses (picker-actions.js's `add-power-core` -> stagePowerCoreOneClick) -- no new
    // staging logic here, just the button. Staging power then lets Build queue the worksite
    // as a staged change (worksite.js worksiteStageOnly) instead of the dead-end H1 refusal.
    const needsPower = p?.state === 'no_project' && row.is_powered === false && !plotPoweredAfterStage(row);
    const powerBtnHtml = needsPower
      ? `<button type="button" data-control-density="header" data-control-mode="icon" class="ceres-btn ceres-btn--primary harvest-bar-btn harvest-bar-power" data-slot-action="add-power-core" data-deed-uid="${esc(row.deed_uid)}" data-group-id="power_core" data-slot-index="0" aria-label="Stage a Power Core"${tipAttrs({ title: 'Stage a Power Core -- unlocks Build, nothing signs yet' })}><img src="${POWER_CORE_IMAGE}" alt="" aria-hidden="true"></button>`
      : '';
    if (!cogHtml && !powerBtnHtml && !text) return '';
    // the
    // mid-height spot () is only for a plot with nothing staged; a staged build keeps the normal spot under it.
    return `<div class="harvest-bar is-quiet${p?.state === 'no_project' && !stagedBuild ? ' is-no-worksite' : ''}${stagedBuild ? ' is-staged-build' : ''}" data-level="ok" data-reveal-group="controls">${cogHtml}${powerBtnHtml}${text ? `<span class="harvest-bar-state">${esc(text)}</span>` : ''}</div>`;
  }
  ensureTicker();
  const token = tokenLabel(p.token);
  const icon = tokenIcon(p.token);
  const gain = netAfterCosts({ token: p.token, net: a.net, costs: a.costs })[0]?.amount;
  const level = pending ? 'ok' : capacityLevel(a.pct);
  const short = !pending && fresh?.affordability?.status === 'short';
  const shownPct = pending ? 0 : a.pct;
  // the amount box is as wide as the plot's largest value (a full week at the current
  // rate), fixed for the life of the render, so ticking or the fresh read never resize the bar.
  const capOf = (q) => (q ? Number(q.gross_per_hour || 0) * (1 - Number(q.tax_rate ?? 0.1)) * CAP_HOURS : 0);
  const capNet = Math.max(capOf(p), capOf(row.harvest), pending ? 0 : a.net);
  const amtCh = Math.max(4, fmt2(capNet).length + 0.5, 'You receive'.length * 0.62);
  const stateAttrs = pending ? '' : ` data-anchor="${esc(p.last_action_time || '')}" data-shown="${gain}"`;
  return `<div class="harvest-bar${pending ? ' is-pending' : ''}${shownPct > 0 ? ' has-cap' : ''}" data-deed-uid="${esc(row.deed_uid)}" data-level="${level}" data-reveal-group="controls"${stateAttrs} style="--pct:${shownPct.toFixed(2)}%">${cogHtml}<span class="harvest-bar-mid" style="--amt-ch:${amtCh.toFixed(1)}ch"${tipAttrs({ title: pending ? 'Reading this plot…' : `You receive ${fmt2(gain)} ${token.toLowerCase()} after tax and same-resource costs` })}>${icon ? `<img class="harvest-bar-res" src="${icon}" alt="${esc(token)}"${inkShiftStyle(icon)}>` : ''}<span class="harvest-bar-stack"><span class="harvest-bar-lbl">You receive</span><span class="harvest-bar-amt" data-hb-amt>${pending ? '' : fmt2(gain)}</span></span></span><button type="button" data-control-density="header" data-control-mode="icon" class="ceres-btn ceres-btn--primary harvest-bar-btn harvest-bar-go${short ? ' is-short' : ''}" data-harvest-action="open-plot" data-deed-uid="${esc(row.deed_uid)}" aria-label="Harvest"${tipAttrs({ title: 'Harvest' })}>${SICKLE_SVG}</button><span class="harvest-bar-edge" role="meter" aria-label="Worksite capacity ${pending ? 'loading' : `${fmt2(a.pct)}% full`}" aria-valuenow="${shownPct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100"><i></i></span></div>`;
}
