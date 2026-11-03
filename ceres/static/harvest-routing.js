import { balancesChanged, onBalancesChanged } from './balances-bus.js';
import { rewardCelebrations } from './harvest-results.js';
import { territorySlug } from './icons.js';
import { esc } from './format.js';
import { tokenIcon, tokenLabel, fmt2 } from './harvest-state.js';
import { walletReady, walletControlsHtml } from './wallet.js';
import { prepareSigningBatch, requestSignatureThroughGate } from './broadcast-gate.js';
import { releaseSignedQueueDependency, signedQueueRows } from './transaction-queue.js';
import { createRoutingSignatureChain } from './routing-signature-chain.js';
import { tipAttrs } from './tooltip.js';
import { resourceSymbolsWhere } from './resources.js';

const ROOT = '/api/harvest/routing/';
const LABELS = { transfer: 'Move to a region', sell: 'Sell harvest', liquidity: 'Add to liquidity pool', liquidity_funding_swap: 'Swap pool resource for DEC', buy_remainder: 'Cover remaining costs' };
const STATE_LABELS = { ready_harvest: 'Harvest review required', planned: 'Waiting for your signature', reviewed: 'Waiting for your signature', confirmed: 'Confirmed', skipped: 'Skipped', pending: 'Confirming with the game', submitted: 'Confirming with the game', settling: 'Waiting for the game to settle the DEC → SPS swap and prepare the SPS funding transfer.', awaiting_wallet: 'Waiting for your signature', failed: 'Needs attention', stopped: 'Needs attention', ready: 'Waiting for your signature', complete: 'Done', cancelled: 'Needs attention', harvest_partial: 'Confirmed portion selected', harvesting: 'Confirming with the game', awaiting_funds: 'Needs attention', empty: 'No allocation' };
const resourceLabel = (symbol) => ['DEC', 'SPS'].includes(symbol) ? symbol : tokenLabel(symbol);
const TERMINAL = new Set(['complete', 'cancelled', 'failed', 'stopped']);
const IN_FLIGHT = new Set(['awaiting_wallet', 'pending', 'submitted', 'settling']);
const CHAINABLE_SALE_SYMBOLS = new Set(resourceSymbolsWhere('regional')); // registry: GRAIN WOOD STONE IRON RESEARCH AURA
const DEFAULT_MAX_OPS_PER_TX = 4;
const ROUTING_RUN_IDS = new Map();
export function mintHarvestRunId(now = new Date()) {
  const minute = now.toISOString().slice(0, 16);
  const hash = (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/-/g, '').slice(0, 8);
  return `run-${minute}-${hash}`;
}
export function routingClientRunId(player, routingRunId) {
  const key = `ceres:transaction-run:v1:${String(player || '').toLowerCase()}:${String(routingRunId || '')}`;
  if (ROUTING_RUN_IDS.has(key)) return ROUTING_RUN_IDS.get(key);
  let runId;
  try { runId = localStorage.getItem(key); } catch (_) {}
  if (!runId) {
    runId = mintHarvestRunId();
    try { localStorage.setItem(key, runId); } catch (_) {}
  }
  ROUTING_RUN_IDS.set(key, runId);
  return runId;
}
const inFlight = run => !!run && !TERMINAL.has(run.state) && (IN_FLIGHT.has(run.state) || (run.steps || []).some(step => IN_FLIGHT.has(step.state)));
const maxOpsPerTx = data => Number.isInteger(data?.max_ops_per_tx) && data.max_ops_per_tx > 0
  ? data.max_ops_per_tx : DEFAULT_MAX_OPS_PER_TX;
const button = (action, text, primary = false, disabled = false) => `<button type="button" class="ceres-btn ceres-btn--${primary ? 'primary' : 'secondary'}" data-control-density="comfortable" data-hr="${action}"${disabled ? ' disabled' : ''}>${text}</button>`;
const removeButton = (action, label) => `<button type="button" class="ceres-btn ceres-btn--secondary hr-remove" data-control-density="compact" data-control-mode="icon" data-control-case="destructive" data-hr="${esc(action)}" aria-label="${esc(label)}"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`;
const option = (value, label, selected) => `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`;
const token = (symbol, value) => `<span class="hr-amount">${tokenIcon(symbol) ? `<img src="${esc(tokenIcon(symbol))}" alt="">` : ''}<b>${fmt2(value)}</b><span>${esc(resourceLabel(symbol))}</span></span>`;

const routingRuleApplies = (rule, scope) => scope.kind === 'all' || rule.source_region === 'inventory' || (scope.region_uids || []).includes(rule.source_region);
// routes are never stored between visits any more, so there is never a saved route to announce on the Harvest button.
export function hasSavedHarvestRouting() { return false; }

export function createHarvestRouting({ player, scope, receipts = [], activeRunId = null, onRender, onBack, onDone, onHarvest, isCurrent }) {
  const storageKey = `ceres:harvest-routing:v1:${player}`;
  const S = { data: null, preview: null, run: null, otherRun: null, adopted: false, review: null, selectedMemberIds: new Set(), selectionInitialized: false, error: '', busy: false, editingRun: false, rules: [], reserves: [], slippage: '2.5', phase: receipts.length ? 'after' : 'before', signatureUpdates: [], chainActive: false };
  const stopBalanceListener = onBalancesChanged(({ assets }) => { if (isCurrent() && !S.busy && !S.chainActive && assets.some(a => ['DEC', 'SPS', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'].includes(a))) api('state').then(data => { if (isCurrent() && !S.busy && !S.chainActive) { S.data = data; onRender(); } }).catch(() => {}); }, { player });
  let migratedLegacyDecSource = false;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
    if (Array.isArray(saved.rules)) S.rules = saved.rules.filter((r) => r && typeof r === 'object' && routingRuleApplies(r, scope)).slice(0, 100);
    if (Array.isArray(saved.reserves)) S.reserves = saved.reserves.filter((r) => r && typeof r === 'object').slice(0, 600);
    if (typeof saved.slippage === 'string') S.slippage = saved.slippage;
    S.rules.forEach(rule => {
      delete rule.include_existing_inventory;
      if (rule.slippage == null) rule.slippage = S.slippage;
      if (rule.kind === 'liquidity' && rule.dec_source === 'harvest') {
        rule.dec_source = 'wallet';
        migratedLegacyDecSource = true;
      }
    });
  } catch (_) { S.error = 'Saved routing preferences could not be read.'; }
  const save = () => {
    try {
      const current = JSON.parse(localStorage.getItem(storageKey) || '{}');
      const outsideScope = Array.isArray(current.rules) ? current.rules.filter(rule => rule && typeof rule === 'object' && !routingRuleApplies(rule, scope)) : [];
      localStorage.setItem(storageKey, JSON.stringify({ rules: [...outsideScope, ...S.rules], reserves: S.reserves, slippage: S.slippage }));
    }
    catch (_) { throw new Error('Site storage is unavailable. Routing preferences could not be saved.'); }
  };
  if (migratedLegacyDecSource) {
    try { save(); } catch (error) { S.error = error.message; }
  }
  const scopeKey = value => value?.kind === 'regions' ? `regions:${[...new Set(value.region_uids || [])].sort().join(',')}` : value?.kind;
  const scopeLabel = value => value?.kind === 'regions' ? (value.region_uids || []).map(regionLabel).join(', ') : 'All regions';
  function selectSavedRun(history) {
    // Only this harvest's own run, or a run whose transaction is still on chain for the same selection
    // (reconcile only — an adopted run never has its remaining steps routed later).
    const own = activeRunId ? history.find(run => run.run_id === activeRunId) || null : null;
    const adopted = !own ? history.find(run => inFlight(run) && scopeKey(run.scope) === scopeKey(scope)) || null : null;
    S.run = own || adopted;
    S.selectionInitialized = false;
    initializePlanSelection();
    S.adopted = !!adopted;
    S.otherRun = null;
  }
  async function stop() {
    stopBalanceListener();
    clearTimeout(syncTimer);
    if (S.run && !TERMINAL.has(S.run.state) && !inFlight(S.run)) {
      const response = await fetch(ROOT + 'stop?player=' + encodeURIComponent(player), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: S.run.run_id }), keepalive: true,
      });
      if (!response.ok) console.error('CERES_ROUTING_STOP_FAILED', S.run.run_id);
      else S.run = await response.json();
    }
    return S.run;
  }
  function clear() {
    stopBalanceListener();
    clearTimeout(syncTimer);
    syncTimer = null;
    pollKey = null;
    pollDeadline = 0;
    syncing = false;
    S.data = null;
    S.preview = null;
    S.run = null;
    S.otherRun = null;
    S.adopted = false;
    S.review = null;
    S.error = '';
    S.busy = false;
    S.editingRun = false;
  }
  async function api(path, body, base = ROOT) {
    const response = await fetch(`${base}${path}?player=${encodeURIComponent(player)}`, {
      ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      ...(path === 'verify' ? { signal: AbortSignal.timeout(20000) } : {}),
    });
    let data;
    try { data = await response.json(); } catch (error) {
      if (response.ok || error.name === 'AbortError' || error.name === 'TimeoutError') throw error;
      data = {};
    }
    if (response.ok && base === ROOT && path === 'verify' && (!data?.run_id || !Array.isArray(data.steps))) throw new Error('Confirmation response is incomplete. Your transaction remains pending.');
    if (!response.ok) throw new Error(data.detail || `Routing request failed (${response.status})`);
    if (base === ROOT && data?.run_id === S.run?.run_id && Array.isArray(data.steps)) data.steps.filter(step => step.state === 'confirmed' && S.run.steps?.find(old => old.step_id === step.step_id)?.state !== 'confirmed').forEach(step => balancesChanged({ player, assets: [step.quote?.symbol, step.quote?.destination_symbol, 'DEC'].filter(Boolean), regions: [step.source_region, step.destination_region].filter(Boolean), id: `routing:${data.run_id}:${step.step_id}` }));
    return data;
  }
  let syncTimer = null;
  let pollKey = null;
  let pollDeadline = 0;
  let syncing = false;
  async function work(callback) {
    if (S.busy || !isCurrent()) return;
    S.busy = true; S.error = ''; onRender();
    try { await callback(); } catch (error) {
      S.error = error.message || String(error);
      S.chainActive = false;
      // Log every post-harvest routing error server-side; the run is closed only by the server on a real step failure.
      if (S.phase === 'after' || S.run?.phase === 'after') api('failure', { reason: S.error }).catch(logError => console.error('CERES_ROUTING_FAILURE_LOG_FAILED', logError));
    }
    finally { S.busy = false; if (isCurrent()) { onRender(); scheduleSync(); } }
  }
  function regionLabel(uid) {
    if (uid === 'inventory') return 'Wallet inventory';
    const row = S.data?.regions.find((r) => r.region_uid === uid);
    return row ? `Region ${String(row.region_number).padStart(3, '0')}` : String(uid || '');
  }
  function regionBadge(uid) {
    if (uid === 'inventory') return 'Wallet inventory';
    const names = { PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' };
    const slug = territorySlug(names[String(uid).split('-')[1]]);
    return `<span class="ch-plot-badge${slug ? ` territory-${slug}` : ''}">${esc(regionLabel(uid))}</span>`;
  }
  const reviewMemberIds = (review = S.review) => review?.member_ids || (review?.step?.step_id ? [review.step.step_id] : []);
  const planSelectableIds = () => (S.run?.steps || []).filter(step => {
    const quote = step.quote || step;
    return step.kind === 'sell' && !step.two_step && !step.depends_on_step_id
      && quote.destination_symbol === 'DEC' && !['confirmed', 'skipped'].includes(step.state);
  }).map(step => step.step_id);
  function initializePlanSelection() {
    if (S.selectionInitialized || !S.run) return;
    S.selectedMemberIds = new Set(planSelectableIds());
    S.selectionInitialized = true;
  }
  function selectedReviewIds(review = S.review) {
    const ids = reviewMemberIds(review);
    const selectable = new Set(planSelectableIds());
    return ids.some(identifier => selectable.has(identifier))
      ? ids.filter(identifier => S.selectedMemberIds.has(identifier)) : ids;
  }
  function setReview(review, preserveSelection = false) {
    const previousSelection = S.selectedMemberIds;
    S.review = review;
    const refreshedIds = reviewMemberIds(review);
    if (!S.selectionInitialized) {
      S.selectedMemberIds = new Set(refreshedIds);
      S.selectionInitialized = true;
    } else if (preserveSelection) {
      S.selectedMemberIds = new Set([...previousSelection].filter(identifier => planSelectableIds().includes(identifier)
        || refreshedIds.includes(identifier)));
    }
  }
  function syncReviewFromRun() {
    const next = S.run?.steps.find(step => !['confirmed', 'skipped'].includes(step.state));
    const group = S.run?.group_review;
    setReview(group?.member_ids?.includes(next?.step_id) ? group : next?.review || null);
  }
  function signatureGroups(record) {
    const groups = [];
    const pending = [];
    const symbols = new Set();
    let pendingIdentity = null;
    const flush = () => {
      if (pending.length) groups.push({ kind: 'routing', member_ids: pending.map(step => step.step_id), steps: [...pending] });
      pending.length = 0; symbols.clear(); pendingIdentity = null;
    };
    const beforeSteps = record.phase_steps?.before || [];
    for (const step of beforeSteps) {
      if (step.state !== 'skipped') groups.push({ kind: 'routing', member_ids: [step.step_id], steps: [step] });
    }
    const activeGroup = new Set(record.group_review?.member_ids || []);
    for (const step of record.steps || []) {
      if (step.state === 'skipped') continue;
      const quote = step.quote || step;
      const symbol = record.phase === 'after' && step.kind === 'sell'
        && quote.destination_symbol === 'DEC' && CHAINABLE_SALE_SYMBOLS.has(quote.source_symbol)
        && !quote.external && !step.two_step && !step.depends_on_step_id ? quote.source_symbol : null;
      if (!symbol) {
        flush();
        groups.push({ kind: 'routing', member_ids: [step.step_id], steps: [step] });
        continue;
      }
      const identity = step.tx_id ? `tx:${step.tx_id}` : activeGroup.has(step.step_id) ? 'review' : 'future';
      if (pending.length && identity !== pendingIdentity) flush();
      if (symbols.has(symbol) || pending.length >= maxOpsPerTx(S.data)) flush();
      pendingIdentity = identity;
      pending.push(step);
      symbols.add(symbol);
    }
    flush();
    if (record.phase !== 'after') {
      const projected = [];
      const projectedSymbols = new Set();
      const flushProjected = () => {
        if (projected.length) groups.push({ kind: 'routing', member_ids: projected.map(item => item.id), steps: [] });
        projected.length = 0; projectedSymbols.clear();
      };
      for (const rule of record.rules || []) {
        const identifier = `rule:${rule.id}`;
        const chainable = rule.kind === 'sell' && rule.destination_symbol === 'DEC' && CHAINABLE_SALE_SYMBOLS.has(rule.symbol);
        if (chainable) {
          if (projectedSymbols.has(rule.symbol) || projected.length >= maxOpsPerTx(S.data)) flushProjected();
          projected.push({ id: identifier });
          projectedSymbols.add(rule.symbol);
          continue;
        }
        flushProjected();
        groups.push({ kind: 'routing', member_ids: [identifier], steps: [] });
        if (rule.kind === 'sell' && rule.destination_symbol === 'SPS' && rule.symbol !== 'DEC') {
          groups.push({ kind: 'routing', member_ids: [`${identifier}:sps`], steps: [] });
        }
        if ((rule.kind === 'sell' && rule.destination_symbol === 'SPS') || (rule.kind === 'liquidity' && rule.symbol === 'SPS')) {
          groups.push({ kind: 'funding', member_ids: [identifier], steps: [] });
        }
      }
      flushProjected();
    }
    const steps = [...beforeSteps, ...(record.steps || [])];
    for (const step of steps) {
      if (step.state === 'skipped') continue;
      const quote = step.quote || step;
      const deposit = step.deposit || {};
      const needsFunding = (step.kind === 'sell' && quote.external && quote.destination_symbol === 'SPS')
        || (step.kind === 'liquidity' && deposit.external && deposit.symbol === 'SPS');
      if (needsFunding) groups.push({ kind: 'funding', member_ids: [step.step_id], steps: [step] });
    }
    const chunks = record.harvest_preview?.chunks;
    const chunkCount = Array.isArray(chunks) ? chunks.length : Number(record.projected_harvest_chunks || 0);
    const skippedChunks = new Set(record.harvest_skipped_chunks || []);
    for (let index = 0; index < chunkCount; index += 1) {
      if (!skippedChunks.has(index)) groups.push({ kind: 'harvest', member_ids: [`harvest:${index}`], steps: [] });
    }
    return groups;
  }
  function selectedSignatureTotal() {
    if (!S.run) return 0;
    const selectable = new Set(planSelectableIds());
    const groups = signatureGroups(S.run);
    const reportedGroups = Array.isArray(S.run.signature_groups) ? S.run.signature_groups : [];
    if (!selectable.size && !reportedGroups.length && groups.length < Number(S.run.signatures_total || 0)) return Number(S.run.signatures_total || 0);
    const plan = reportedGroups.length > groups.length ? reportedGroups : groups;
    return plan.reduce((total, group) => {
      const optional = group.member_ids.filter(identifier => selectable.has(identifier));
      return total + (optional.length ? Number(optional.some(identifier => S.selectedMemberIds.has(identifier))) : 1);
    }, 0);
  }
  function stepLabel(step, detail) {
    const role = step.two_step?.role;
    if (role === 'first') return `Sell ${resourceLabel(step.symbol)} → DEC (step 1 of 2, then DEC → ${resourceLabel(step.two_step.final_symbol)})`;
    if (role === 'second') return `Swap DEC → ${resourceLabel(detail.destination_symbol || detail.quote?.destination_symbol)} (step 2 of 2 · uses the actual DEC from step 1)`;
    return LABELS[step.kind] || step.kind;
  }
  function decSourceProvenance(detail) {
    const funding = detail.dec_funding || {};
    if (detail.dec_source === 'wallet') return `DEC from wallet · balance ${fmt2(funding.available || 0)}`;
    const split = funding.split || {};
    const share = Number(split.total_resource) > 0 ? Math.ceil(Number(split.swap_resource) / Number(split.total_resource) * 100) : 51;
    const extra = Number(funding.extra_signatures || (detail.deposit?.external ? 2 : 1));
    return `Sell ${share} % of the reserved ${resourceLabel(detail.symbol)} for DEC first · +${extra} ${extra === 1 ? 'signature' : 'signatures'}`;
  }
  function decSourceChoices(step, detail, confirmed) {
    if (detail.kind !== 'liquidity' || !detail.dec_source) return '';
    const disabled = confirmed || S.busy || (!!S.run && !S.editingRun);
    const choices = [
      ['swap_half', 'Sell half first', 'Sell half of the resource going into the pool for DEC first, so the pair is balanced.', 'Half of the resource you are putting into the pool is sold for DEC first, so the pair is balanced.'],
      ['wallet', 'Wallet', 'Use DEC from your wallet balance.', 'The DEC comes from your wallet balance.'],
    ];
    const choice = ([value, title, description, tooltip]) => `<button type="button" class="ceres-btn ceres-btn--choice hr-dec-source-choice" data-control-density="compact" data-control-wrap-labels data-hr-dec-source="${value}" data-rule="${esc(detail.rule_id || '')}" aria-pressed="${detail.dec_source === value}"${disabled ? ' disabled' : ''}${tipAttrs({ title, body: tooltip })}><span class="hr-dec-source-choice-copy"><b>${title}</b><small>${description}</small></span></button>`;
    const funding = detail.dec_funding || {};
    const shortage = Number(funding.shortfall || 0);
    const split = funding.split || {};
    return `<fieldset class="hr-dec-source"><legend>Where should the DEC come from?</legend>
      <div class="ceres-btn-group hr-dec-source-options" data-control-equal data-control-wrap-labels>${choices.map(choice).join('')}</div>
      <p class="hr-dec-provenance">${esc(decSourceProvenance(detail))}</p>
      ${split.swap_resource ? `<p>Reserve ${fmt2(split.total_resource)} ${esc(resourceLabel(detail.symbol))}; swap ${fmt2(split.swap_resource)} (protected ≥ ${fmt2(split.protected_dec)} DEC); then deposit ${fmt2(split.deposit_resource)} + ${fmt2(split.required_dec)} DEC.</p>` : ''}
      ${funding.confirmed_dec != null ? `<p>Funding swap delivered ${fmt2(funding.confirmed_dec)} DEC; this deposit uses ${fmt2(funding.required)} DEC; ${fmt2(funding.remainder_dec)} DEC remains.</p>` : ''}
      ${shortage > 0 ? `<p class="hr-error" role="alert">Short by ${fmt2(shortage)} DEC from the selected source.</p>` : ''}
    </fieldset>`;
  }
  function stepHtml(step) {
    const detail = step.review?.step || step;
    const q = detail.quote || detail;
    const state = step.state || step.status || 'planned';
    const confirmed = state === 'confirmed';
    const received = confirmed ? step.receipt?.received || [] : [];
    const deposited = confirmed ? step.receipt?.deposited || [] : [];
    const content = confirmed && deposited.length ? deposited.map(item => token(item.symbol, item.amount)).join('<span>+</span>')
      : q.input ? `${token(q.source_symbol, q.input)}<span>→</span>${received.length ? received.map(item => token(item.symbol, item.amount)).join('') : token(q.destination_symbol, q.output)}`
      : detail.deposit ? `${token(detail.deposit.symbol, detail.deposit.resource_amount)}<span>+</span>${token('DEC', detail.deposit.dec_amount)}`
      : `<span>${esc(step.reason || 'No allocation')}</span>`;
    const tone = confirmed ? 'confirmed' : state === 'failed' ? 'failed' : ['submitted', 'pending', 'awaiting_wallet', 'settling'].includes(state) ? 'pending' : state === 'reviewed' ? 'ready' : 'planned';

    const headline = stepLabel(step, detail);
    const selectable = ['planned', 'reviewed'].includes(state) && planSelectableIds().includes(step.step_id);
    const heading = selectable ? `<label class="hm-check"><input class="hm-rcheck" type="checkbox" data-hr-member="${esc(step.step_id)}"${S.selectedMemberIds.has(step.step_id) ? ' checked' : ''}><strong>${esc(headline)}</strong></label>` : `<strong>${esc(headline)}</strong>`;
    return `<article class="hr-step is-${tone}"><header>${heading}<span class="hr-step-status">${tone === 'pending' ? '<span class="spinner spinner-sm" aria-hidden="true"></span>' : ''}${esc(STATE_LABELS[state] || state)}</span></header><small>${confirmed && (received.length || deposited.length) ? 'Confirmed result' : confirmed ? 'Confirmed · quoted amounts shown; receipt amounts unavailable' : 'Estimated return'}</small><div class="hr-flow">${content}</div>${q.estimate && state === 'planned' ? '<p class="hm-hint">≈ estimate until step 1 confirms</p>' : ''}<p>${regionBadge(step.source_region)}${step.destination_region ? ` → ${regionBadge(step.destination_region)}` : ''}</p>${decSourceChoices(step, detail, confirmed)}
      ${q.price_impact_large && !confirmed ? '<p class="hr-warning">Large trade relative to the pool. Review the quoted return carefully.</p>' : ''}${q.external && !confirmed ? '<p class="hr-warning">SPS bridge settlement is required after game confirmation.</p>' : ''}${step.error ? `<p class="hr-error">${esc(step.error)}</p>` : ''}
      <details><summary>Transaction details</summary>${q.minimum ? `<p>Minimum received: ${fmt2(q.minimum)} ${esc(resourceLabel(q.destination_symbol))} · fees included</p>` : ''}${detail.deposit && !confirmed && S.slippage != null ? `<p>Up to ${fmt2(detail.dec_funding?.up_to ?? (Number(detail.deposit.dec_amount) * (1 + Number(S.slippage) / 100)))} ${esc(resourceLabel('DEC'))} · maximum with slippage</p>` : ''}${q.price_impact_large ? '<p class="hr-warning">Large trade relative to the pool. Review the quoted return carefully.</p>' : ''}${q.external ? '<p class="hr-warning">SPS bridge settlement is required after game confirmation.</p>' : ''}${step.review?.operations ? `<pre>${esc(JSON.stringify(step.review.operations, null, 2))}</pre>` : ''}${/^[a-f0-9]{40}$/i.test(step.tx_id || '') ? `<a href="https://hivehub.dev/tx/${step.tx_id}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}${/^[a-f0-9]{40}$/i.test(step.bridge_transaction_id || '') ? `<a href="https://hivehub.dev/tx/${step.bridge_transaction_id}" target="_blank" rel="noopener noreferrer">View bridge completion ↗</a>` : ''}</details></article>`;
  }
  function failedStepSkippable(step, contextError) {
    if (step?.state === 'failed') {
      return (step.wallet_outcome === 'not_sent' && !step.tx_id) || (!!step.tx_id && typeof step.error === 'string' && !!step.error) || step.wallet_outcome === 'expired';
    }
    // a dependent second leg stuck at 'planned' because its confirmed first leg exposed
    // no readable DEC amount is also skippable — the server accepts skip-failed for this exact case.
    return step?.state === 'planned' && !!step.depends_on_step_id
      && typeof contextError === 'string' && contextError.includes('DEC proceeds are unknown');
  }
  function signingVisible() {
    if (S.adopted || S.chainActive || S.error) return false;
    const next = S.run?.steps.find(step => !['confirmed', 'skipped'].includes(step.state));
    return !!S.review && next?.state === 'reviewed';
  }
  function signingOperationHtml(update) {
    const operations = update?.item?.handoff?.operations || update?.item?.envelope?.transaction?.operations || [];
    const rows = operations.map(([type, body]) => {
      let visible = body;
      try { visible = { ...body, json: JSON.parse(body.json) }; } catch (_) {}
      return `<li><strong>${esc(type)}</strong><pre>${esc(JSON.stringify(visible, null, 2))}</pre></li>`;
    }).join('');
    return `<section class="hr-signing-operation" aria-label="Exact operation in the wallet request"><header><h3>Signing now</h3><span>${operations.length} operation${operations.length === 1 ? '' : 's'} · Posting authority</span></header><p>These are the exact operation${operations.length === 1 ? '' : 's'} in the request currently in front of Keychain.</p><ol>${rows || '<li>No operation preview is available; signing is blocked.</li>'}</ol></section>`;
  }
  function signingRunHtml() {
    const updates = S.signatureUpdates.slice().sort((a, b) => a.index - b.index);
    const confirmedIds = new Set((S.run?.steps || []).filter(step => step.state === 'confirmed').map(step => step.step_id));
    const progress = updates.map(update => {
      const ids = update.item?.memberIds || [update.item?.handoff?.step_id];
      const confirmed = ids.some(id => confirmedIds.has(id));
      const state = confirmed ? 'confirmed' : update.state === 'signing' ? 'current' : update.state === 'signed' || update.state === 'submitted' ? 'signed' : 'attention';
      const label = state === 'confirmed' ? 'Confirmed' : state === 'current' ? 'Signing now' : state === 'signed' ? 'Signed · awaiting confirmation' : (update.state || 'Needs attention');
      return `<li class="hr-signing-progress is-${state}"><span class="hr-signing-dot" aria-hidden="true"></span><span><strong>Request ${update.index + 1}</strong><small>${esc(label)}</small></span></li>`;
    }).join('');
    const current = updates.find(update => update.state === 'signing') || updates.at(-1);
    return `<section class="hr-signing-focus" aria-label="Signing run"><header><div><h2>Signing routing changes</h2><p>Only this signing run is shown until every request is handled.</p></div><span class="hr-signing-live" role="status" aria-live="polite">${current?.state === 'signing' ? 'Waiting for Keychain' : 'Preparing next request'}</span></header>${current ? signingOperationHtml(current) : '<p class="hr-signing-preparing" role="status"><span class="spinner spinner-sm" aria-hidden="true"></span>Preparing the first wallet request…</p>'}<ol class="hr-signing-progress" aria-label="Signing progress">${progress}</ol></section>`;
  }
  function signingStatusHtml() {
    if (!S.run) return '';
    const total = Number(S.run.signatures_total || 0);
    const signed = Number(S.run.signed_signatures || 0);
    const confirmed = Number(S.run.confirmed_signatures || 0);
    const next = S.run.steps?.find(step => !['confirmed', 'skipped'].includes(step.state));
    const liveSigning = S.signatureUpdates.some(update => update.state === 'signing');
    const state = S.error || S.run.state === 'failed' || ['failed', 'stopped', 'cancelled', 'awaiting_funds'].includes(next?.state)
      ? 'Needs attention' : S.run.state === 'complete' ? 'Done'
      : next?.state === 'settling' ? 'Waiting for the game to settle the DEC → SPS swap and prepare the SPS funding transfer.'
      : ['submitted', 'pending'].includes(next?.state) ? 'Confirming with the game'
      : liveSigning || next ? 'Waiting for your signature' : 'Done';
    return `<div class="hr-signing-status" role="status" aria-live="polite"><strong>Signed ${signed} of ${total} · Confirmed ${confirmed} of ${total}</strong><span>${esc(state)}</span>${S.error ? `<span class="hr-error">${esc(S.error)}</span>` : ''}</div>`;
  }
  function signingHtml() {
    if (S.adopted) return '';
    if (!signingVisible()) return '';
    const total = selectedSignatureTotal();
    return `<span class="wallet-sign-pair">${walletControlsHtml(player, 'harvest-routing')}${button('sign', `Sign routing · ${total} approvals`, true, S.busy || total === 0 || !S.data?.signing_enabled || !walletReady(player))}</span>`;
  }
  function footerHtml() {
    if (!S.run) return '';
    if (S.chainActive) return `<footer class="hm-foot hr-bundle-action hr-signing-footer">${signingStatusHtml()}</footer>`;
    // the top-of-panel error (rendered separately, far above this footer) is easy to
    // miss right before the sign click, so repeat it here next to the button that needs it.
    return `<footer class="hm-foot hr-bundle-action">${signingStatusHtml()}${signingHtml()}</footer>`;
  }
  async function prepareReceiptRouting() {
    if (S.run || S.otherRun || !receipts.length || !S.rules.length) return;
    const preview = await api('preview', { scope, phase: 'after', rules: S.rules, reserves: S.reserves, slippage: S.slippage, harvest_attempt_ids: receipts });
    if (!isCurrent()) return;
    S.preview = preview;
  }
  function harvestFinished() {
    const attempts = S.run?.harvest_attempt_records || [];
    return S.run?.harvest_closed || (S.run?.harvest_preview?.chunks?.length && attempts.length === S.run.harvest_preview.chunks.length && attempts.every(item => item.evidence?.state === 'reconciled' && item.evidence.members?.length && item.evidence.members.every(member => member.outcome === 'success' && member.game_state === 'accepted' && member.evidence?.length)));
  }
  function fundingPrepared(step) {
    const effects = step?.game_result?.result?.data || {};
    return step?.state === 'settling' && !!(effects.external_swap_required || effects.pending_deposit_id);
  }
  async function signFundingStep(step) {
    const runId = S.run.run_id;
    const fundingRequest = (action, extra = {}) => api('funding', { action, run_id: runId, step_id: step.step_id, ...extra });
    if (!step.funding || ['not_sent', 'expired'].includes(step.funding.state)) {
      S.run = await fundingRequest('review');
      step = S.run.steps.find(item => item.step_id === step.step_id);
    }
    if (step?.funding?.state !== 'reviewed') return;
    const handoff = await fundingRequest('handoff', { review_digest: step.funding.review.digest });
    let envelope;
    try {
      envelope = (await prepareSigningBatch(player, [handoff.operations], handoff.authority,
        { runId: routingClientRunId(player, runId), runKind: 'harvest', maxOpsPerTx: maxOpsPerTx(S.data) }))?.[0] || null;
    } catch (error) {
      await fundingRequest('not-sent', { broadcasted: false });
      throw error;
    }
    if (envelope && step.tx_id) envelope.dependsOn = step.tx_id;
    const chain = createRoutingSignatureChain({
      getId: item => item.envelope?.txId || item.handoff.step_id,
      onStepChange: update => {
        S.signatureUpdates = [...S.signatureUpdates.filter(item => item.id !== update.id), update];
        if (isCurrent()) onRender();
      },
      sign: async item => {
        if (!isCurrent() || !walletReady(player)) {
          await fundingRequest('not-sent', { broadcasted: false });
          throw new Error('The player or wallet changed before SPS funding. Nothing was sent.');
        }
        try {
          const result = await requestSignatureThroughGate(player, item.handoff.operations, null,
            item.handoff.authority, item.envelope);
          item.txId = result.txId;
          try { S.run = await fundingRequest('transaction', { tx_id: result.txId }); }
          catch (error) { error.txId = result.txId; error.signedResult = result; throw error; }
          return result;
        } catch (error) {
          if (!error.txId && error.broadcasted === false) await fundingRequest('not-sent', { broadcasted: false });
          throw error;
        }
      },
    });
    await chain.run([{ handoff, envelope, memberIds: [step.step_id], funding: true }]);
  }
  function scheduleSync() {
    clearTimeout(syncTimer);
    if (!isCurrent() || S.busy || syncing || !S.run || S.run.routing_closed || TERMINAL.has(S.run.state)) return;
    const next = S.run.steps.find(step => step.tx_id && ['submitted', 'pending', 'settling'].includes(step.state)) || S.run.steps.find(step => !['confirmed', 'skipped'].includes(step.state));
    const autoFunding = S.chainActive && fundingPrepared(next)
      && (!next.funding || ['reviewed', 'not_sent', 'expired'].includes(next.funding.state));
    const fundingPending = next?.funding?.tx_id && ['submitted', 'pending'].includes(next.funding.state);
    const pending = next?.tx_id && ['submitted', 'pending', 'settling'].includes(next.state);
    if (pending && pollKey !== next.step_id) { pollKey = next.step_id; pollDeadline = Date.now() + 90000; }
    if (pending && !autoFunding && !fundingPending && Date.now() >= pollDeadline) return;
    // Verify-only: an adopted run may poll/verify its in-flight step, but never auto-advances to the next planned one.
    if (S.adopted && !pending) return;
    const prepareAfter = !S.adopted && S.run.phase === 'harvest' && harvestFinished() && !S.editingRun;
    const autoSign = S.chainActive && next?.state === 'reviewed' && !!S.review;
    if (!pending && !autoFunding && !autoSign && (S.error || (!prepareAfter && next?.state !== 'planned'))) return;
    syncTimer = setTimeout(async () => {
      if (!isCurrent() || S.busy) return;
      if (autoSign) { await action('sign', null); return; }
      syncing = true;
      await work(async () => {
        if (autoFunding) await signFundingStep(next);
        else if (fundingPending) {
          S.run = await api('funding', { action: 'verify', run_id: S.run.run_id, step_id: next.step_id });
          const refreshed = S.run.steps.find(step => step.step_id === next.step_id);
          if (refreshed?.funding?.state === 'funded') pollDeadline = Date.now() + 90000;
        }
        else if (pending) { S.run = await api('verify', { run_id: S.run.run_id }); syncReviewFromRun(); }
        else if (prepareAfter) {
          const preview = await api('preview', { routing_run_id: S.run.run_id });
          if (!isCurrent()) return;
          S.preview = preview;
          S.editingRun = true;
        } else {
          const result = await api('review', { run_id: S.run.run_id });
          S.run = result.run; setReview(result.review);
        }
      });
      syncing = false;
      scheduleSync();
    }, pending && !autoFunding ? 2500 : 0);
  }
  function field(rule, name, label, control) {
    return `<label class="hr-field hr-field--${name}${name === 'quantity' ? ` hr-field--${esc(rule.quantity_mode)}` : ''}"><span>${label}</span>${control.replace('DATA', `data-hr-field="${name}" data-rule="${esc(rule.id)}"`)}</label>`;
  }
  function reserveHtml() {
    const locations = [{ region_uid: 'inventory', balances: S.data.inventory }, ...S.data.regions];
    return `<details class="hr-reserves"${S.reserves.length ? ' open' : ''}><summary>Keep resources in reserve${S.reserves.length ? ` · ${S.reserves.length}` : ''}</summary><p>Harvest costs and a two-minute margin stay protected automatically. Set a higher minimum balance to keep extra resources.</p>${S.reserves.map((reserve, index) => {
      const location = locations.find((row) => row.region_uid === reserve.region_uid);
      return `<div class="hr-reserve"><div class="hr-fields">
        <label class="hr-field"><span>Location</span><select data-hr-reserve="region_uid" data-index="${index}">${locations.map((row) => option(row.region_uid, regionLabel(row.region_uid), reserve.region_uid)).join('')}</select></label>
        <label class="hr-field"><span>Resource</span><select data-hr-reserve="symbol" data-index="${index}">${Object.keys(location?.balances || {}).map((symbol) => option(symbol, resourceLabel(symbol), reserve.symbol)).join('')}</select></label>
        <label class="hr-field"><span>Minimum to keep</span><input data-hr-reserve="amount" data-index="${index}" type="number" min="0" step="0.001" inputmode="decimal" value="${esc(reserve.amount)}"></label>
        ${removeButton(`remove-reserve:${index}`, 'Remove reserve')}</div></div>`;
    }).join('')}${button('add-reserve', 'Add reserve')}</details>`;
  }
  function reserveChange(target) {
    if (S.busy || S.run) return;
    const reserve = S.reserves[Number(target.dataset.index)];
    if (!reserve || !['region_uid', 'symbol', 'amount'].includes(target.dataset.hrReserve)) return;
    reserve[target.dataset.hrReserve] = target.value;
    if (target.dataset.hrReserve === 'region_uid') {
      const balances = reserve.region_uid === 'inventory' ? S.data.inventory : S.data.regions.find((row) => row.region_uid === reserve.region_uid)?.balances;
      reserve.symbol = Object.keys(balances || {})[0] || '';
    }
    S.preview = null;
    try { save(); } catch (error) { S.error = error.message; }
    if (target.tagName === 'SELECT') onRender();
    else { target.closest('.harvest-routing')?.querySelector('.hr-steps')?.remove(); target.closest('.harvest-routing')?.querySelector('[data-hr="start"]')?.remove(); }
  }
  function rulesHtml() {
    const regions = (S.data?.regions || []).filter((region) => scope.kind === 'all' || (scope.region_uids || []).includes(region.region_uid));
    const destinations = S.data?.regions || [];
    const symbols = (S.data?.pools || []).map((pool) => pool.symbol);
    const decSourceChoice = (rule, value, title, description, tooltip) => `<button type="button" class="ceres-btn ceres-btn--choice hr-dec-source-choice" data-control-density="compact" data-control-wrap-labels data-hr-dec-source="${value}" data-rule="${esc(rule.id)}" aria-pressed="${rule.dec_source === value}"${tipAttrs({ title, body: tooltip })}><span class="hr-dec-source-choice-copy"><b>${title}</b><small>${description}</small></span></button>`;
    return `<section class="hr-editor"><h3>After harvest</h3><p class="hm-hint">Allocations use confirmed new harvest. Each source region keeps enough resources for its current harvest costs.</p>${S.rules.map((rule) => `<article class="hr-rule"><div class="hr-rule-heading"><h4>Routing rule</h4>${removeButton(`remove:${rule.id}`, 'Remove routing rule')}</div><div class="hr-fields">
      ${field(rule, 'kind', 'Action', `<select DATA>${['transfer', 'sell', 'liquidity'].map((kind) => option(kind, LABELS[kind], rule.kind)).join('')}</select>`)}
      ${field(rule, 'source_region', 'From', `<select DATA>${regions.map((r) => option(r.region_uid, regionLabel(r.region_uid), rule.source_region)).join('')}${option('inventory', 'Wallet inventory', rule.source_region)}</select>`)}
      ${field(rule, 'symbol', 'Resource', `<select DATA>${symbols.filter((symbol) => rule.source_region === 'inventory' ? ['SPS', 'VOUCHER'].includes(symbol) : !['SPS', 'VOUCHER'].includes(symbol)).map((symbol) => option(symbol, resourceLabel(symbol), rule.symbol)).join('')}</select>`)}
      ${field(rule, 'quantity_mode', 'Allocate', `<select DATA>${option('percent', 'Percentage of harvest', rule.quantity_mode)}${option('amount', 'Fixed amount', rule.quantity_mode)}</select>`)}
      ${field(rule, 'quantity', rule.quantity_mode === 'percent' ? 'Percent' : 'Amount', `<input DATA type="number" inputmode="decimal" min="0" step="0.001" value="${esc(rule.quantity)}">`)}
      ${rule.kind === 'sell' ? field(rule, 'destination_symbol', 'Receive', `<select DATA>${['DEC', ...symbols].map((symbol) => option(symbol, resourceLabel(symbol), rule.destination_symbol)).join('')}</select>`) : ''}
      ${rule.kind === 'transfer' || (rule.kind === 'sell' && !['DEC', 'SPS', 'VOUCHER'].includes(rule.destination_symbol)) ? field(rule, 'destination_region', 'Destination', `<select DATA>${destinations.map((r) => option(r.region_uid, regionLabel(r.region_uid), rule.destination_region)).join('')}</select>`) : ''}
      ${rule.kind !== 'liquidity' ? field(rule, 'slippage', 'Slippage (%)', `<input DATA type="number" inputmode="decimal" min="0.5" max="25" step="0.1" value="${esc(rule.slippage ?? S.slippage)}">`) : `<fieldset class="hr-dec-source hr-dec-source--rule"><legend>DEC source</legend><div class="ceres-btn-group hr-dec-source-options" data-control-equal data-control-wrap-labels>${decSourceChoice(rule, 'swap_half', 'Sell half first', 'Sell half of the resource going into the pool for DEC first.', 'Half of the resource you are putting into the pool is sold for DEC first, so the pair is balanced.')}${decSourceChoice(rule, 'wallet', 'Wallet', 'Use DEC from your wallet balance.', 'The DEC comes from your wallet balance.')}</div><small>${rule.dec_source ? 'The review card will show the available balance and maximum DEC.' : 'The review will use Sell half first unless you choose Wallet.'}</small></fieldset>`}
      </div></article>`).join('')}
      ${button('add', 'Add routing rule')}
      </section>`;
  }
  function previewPlanHtml(startLabel) {
    if (!S.preview) return '';
    const contents = S.preview.steps.length ? S.preview.steps.map(stepHtml).join('') : '<p class="hr-check-result" role="status">No resource allocations are needed for this harvest.</p>';
    const issues = S.preview.blocked && S.preview.issues?.length ? `<p class="hr-error">${esc(S.preview.issues.join(' · '))}</p>` : '';
    return `<div class="hr-steps">${contents}${issues}</div>${button('start', startLabel, true, S.busy || S.preview.blocked)}`;
  }
  function fundingHtml(step) {
    if (step?.state !== 'settling') return '';
    const effects = step.game_result?.result?.data || {};
    if (!(effects.external_swap_required || effects.pending_deposit_id)) return `<p class="hm-statusline" role="status"><span class="spinner spinner-sm" aria-hidden="true"></span>Waiting for the game to settle the DEC → SPS swap and prepare the SPS funding transfer.</p>`;
    const funding = step.funding;
    const review = funding?.review;
    const txLink = /^[a-f0-9]{40}$/i.test(funding?.tx_id || '') ? `<a href="https://hivehub.dev/tx/${funding.tx_id}" target="_blank" rel="noopener noreferrer">View SPS funding transaction ↗</a>` : '';
    return `<section class="hr-review"><h3>SPS funding</h3>
      ${review ? `<div class="hr-flow">${token('SPS', review.amount)}<span>→ @${esc(review.recipient)}</span></div><p>Active authority · ${esc(review.payload.params.memo)}</p>` : '<p>The game has prepared this operation. Review the separate SPS funding transfer to continue.</p>'}
      ${funding?.state === 'expired' ? `<p class="hr-error">${esc(funding.error)}</p>` : ''}
      ${!S.chainActive && (!funding || ['reviewed', 'not_sent', 'expired'].includes(funding.state)) ? button('funding-review', funding?.state === 'reviewed' ? 'Refresh funding review' : 'Review SPS funding', false, S.busy) : ''}
      ${!S.chainActive && funding?.state === 'reviewed' ? `<details><summary>Show Hive operation</summary><pre>${esc(JSON.stringify(review.operations, null, 2))}</pre></details>${button('funding-sign', 'Sign SPS funding · Active', true, S.busy || !S.data.signing_enabled || !walletReady(player))}` : ''}
      ${funding?.state === 'awaiting_wallet' && !funding.tx_id ? `<p>The wallet outcome is unknown. Recover the transaction before continuing.</p><label class="hr-field"><span>SPS funding transaction ID</span><input data-hr-funding-recovery autocomplete="off" spellcheck="false"></label>${button('funding-recover', 'Check funding transaction', false, S.busy)}` : ''}
      ${funding?.tx_id && ['submitted', 'pending'].includes(funding.state) ? `<p class="hm-statusline" role="status"><span class="spinner spinner-sm" aria-hidden="true"></span>Waiting for the validator to confirm the SPS transfer.</p>${S.chainActive ? '' : button('funding-verify', 'Check SPS funding', false, S.busy)}` : ''}
      ${funding?.state === 'failed' ? `<p>This SPS transfer was refused. If you completed funding for this same operation in the game, attach its successful transaction to resume.</p><label class="hr-field"><span>Successful SPS funding transaction ID</span><input data-hr-confirmed-funding autocomplete="off" spellcheck="false"></label>${button('funding-adopt', 'Verify completed funding', false, S.busy)}` : ''}
      ${funding?.state === 'expired' ? `<p>The validator never saw this transfer. If it did land, attach its successful transaction ID; otherwise review SPS funding again.</p><label class="hr-field"><span>Successful SPS funding transaction ID</span><input data-hr-confirmed-funding autocomplete="off" spellcheck="false"></label>${button('funding-adopt', 'Verify completed funding', false, S.busy)}` : ''}
      ${funding?.state === 'funded' ? '<p>SPS funding confirmed. Waiting for the game bridge to complete the operation.</p>' : ''}
      ${funding?.error && funding.state !== 'expired' ? `<p class="hr-error">${esc(funding.error)}</p>` : ''}${txLink}
    </section>`;
  }
  function releasableDependency() {
    try {
      const rows = signedQueueRows(player);
      const runTxIds = new Set((S.run?.steps || []).filter(item => ['submitted', 'pending'].includes(item.state))
        .map(item => item.tx_id).filter(Boolean));
      return rows.find(child => {
        const dependency = child?.dependsOn || child?.releasedDependency;
        const parent = dependency && rows.find(row => row.txId === dependency);
        const parentExpired = parent?.transaction?.expiration
          && Date.parse(parent.transaction.expiration + 'Z') <= Date.now() + 5000;
        const queued = child.state === 'queued' && child.dependsOn === dependency;
        const pendingSync = child.state === 'expired' && child.releaseOutcome === 'not_sent'
          && child.releasedDependency === dependency && !child.signedTransaction;
        return runTxIds.has(child.txId) && !child.submissionAttempted && (queued || pendingSync)
          && parent?.state === 'unknown' && parentExpired;
      }) || null;
    } catch (_) { return null; }
  }
  function needsAttentionHtml(step) {
    const blocked = releasableDependency();
    const failed = (S.run?.steps || []).find(item => item.state === 'failed');
    if (!S.error && !blocked && !failed) return '';
    return `<section class="hr-review" role="alert"><h3>Needs attention</h3>${S.error ? `<p class="hr-error">${esc(S.error)}</p>` : ''}${failed?.error ? `<p class="hr-error">${esc(failed.error)}</p>` : ''}${blocked ? `<p>An earlier submission is still unknown. Release this never-sent dependent transaction to continue safely.</p>${button('release', 'Release', false, S.busy)}` : ''}</section>`;
  }
  function savedRulesHtml() {
    const rules = S.run?.rules || [];
    return `<section class="hr-saved-rules"><h3>After this harvest</h3>${rules.length ? `<ul>${rules.map(rule => `<li><strong>${esc(LABELS[rule.kind] || rule.kind)}</strong><span>${rule.quantity_mode === 'percent' ? `${esc(rule.quantity)}% of harvested` : esc(rule.quantity)} ${tokenIcon(rule.symbol) ? `<img class="hr-summary-icon" src="${esc(tokenIcon(rule.symbol))}" alt="">` : ''}${esc(resourceLabel(rule.symbol))} · ${regionBadge(rule.source_region)}${rule.kind === 'sell' ? ` → ${esc(resourceLabel(rule.destination_symbol))}` : rule.kind === 'transfer' ? ` → ${regionBadge(rule.destination_region)}` : ''}</span></li>`).join('')}</ul>` : '<p>No after-harvest routing rules.</p>'}</section>`;
  }
  function harvestRewardsHtml() {
    const seen = new Set();
    return (S.run?.harvest_reward_members || (S.run?.harvest_attempt_records || []).flatMap(attempt => attempt.evidence?.members || [])).map(member => {
      const finds = (member.finds || []).filter(find => {
        const key = `${member.region_uid}:${find.deed_uid || ''}:${find.kind}:${find.uid || find.type}`;
        if (seen.has(key)) return false;
        seen.add(key); return true;
      });
      return finds.length ? `<section><p>${regionBadge(member.region_uid)}</p>${rewardCelebrations({ ...member, finds })}</section>` : '';
    }).join('');
  }
  function receiptHtml() {
    if (!S.run) return '';
    const steps = [...(S.run.phase_steps?.before || []), ...S.run.steps];
    if (!steps.length && !S.run.harvest_summary) return '';
    const confirmed = steps.filter((step) => step.state === 'confirmed').length;
    const skipped = steps.filter((step) => step.state === 'skipped').length;
    const waiting = steps.filter((step) => !['confirmed', 'skipped'].includes(step.state)).length;
    const summary = S.run.harvest_summary;
    const next = S.run.steps.find(step => !['confirmed', 'skipped'].includes(step.state));
    const signaturesTotal = Number(S.run.signatures_total ?? S.run.total_signatures ?? 0);
    const signaturesSigned = Number(S.run.signed_signatures ?? 0);
    return `<section class="hr-receipt" aria-label="Routing progress"><h3>${S.run.state === 'complete' ? 'Routing complete' : 'Routing progress'}</h3><p><strong>${signaturesTotal} wallet approvals to complete this routing</strong></p><div class="hr-receipt-counts"><span><b>${confirmed}</b> confirmed</span>${waiting ? `<span><b>${waiting}</b> remaining</span>` : ''}${skipped ? `<span><b>${skipped}</b> skipped</span>` : ''}${signaturesTotal ? `<span><b>${signaturesSigned} of ${signaturesTotal}</b> signatures complete</span>` : ''}</div>${summary ? `<p>${summary.accepted_regions.length} harvest operations confirmed${summary.refused_regions.length ? ` · ${summary.refused_regions.length} not completed` : ''}${summary.skipped_chunks.length ? ` · ${summary.skipped_chunks.length} batches skipped` : ''}</p>` : ''}</section>${needsAttentionHtml(next)}`;
  }
  function html() {
    if (S.chainActive) return `<section class="harvest-routing hr-signing-only">${signingRunHtml()}</section>`;
    if (S.run?.routing_closed) {
      return '<section class="harvest-routing"><h3>Routing failed</h3><p class="hr-error" role="alert">' + esc(S.run.failure?.message || 'Ceres did not complete routing.') + '</p><p>This failure is logged; remaining steps will not run later.</p>'
        + receiptHtml() + [...(S.run.phase_steps?.before || []), ...S.run.steps].map(stepHtml).join('')
        + button('done', 'Done', true, S.busy) + '</section>';
    }
    if (S.adopted && !inFlight(S.run)) {
      return '<section class="harvest-routing"><h3>Routing progress</h3>' + receiptHtml() + [...(S.run.phase_steps?.before || []), ...S.run.steps].map(stepHtml).join('')
        + '<p>This run’s transaction has been confirmed. Remaining steps will not be routed later.</p>'
        + button('done', 'Done', true, S.busy) + '</section>';
    }
    const next = S.run?.steps.find(step => step.tx_id && ['submitted', 'pending', 'settling'].includes(step.state)) || S.run?.steps.find((step) => !['confirmed', 'skipped'].includes(step.state));
    const attempts = S.run?.harvest_attempt_records || [];
    const harvestDone = S.run?.harvest_closed || (S.run?.harvest_preview?.chunks?.length && attempts.length === S.run.harvest_preview.chunks.length && attempts.every((item) => item.evidence?.state === 'reconciled' && item.evidence.members?.length && item.evidence.members.every((member) => member.outcome === 'success' && member.game_state === 'accepted' && member.evidence?.length)));
    const terminalAttempts = attempts.length && attempts.every(({ evidence }) => evidence && ((!evidence.tx_id && evidence.state === 'rejected_before_send') || (evidence.tx_id && evidence.members?.length && evidence.members.every((member) => (member.outcome === 'success' && member.game_state === 'accepted' && member.evidence?.length) || (member.outcome === 'failed' && member.game_state === 'refused')))));
    const hasProceeds = attempts.some(({ evidence }) => evidence?.members?.some((member) => member.outcome === 'success' && member.game_state === 'accepted' && member.evidence?.length));
    return `<section class="harvest-routing"><div class="hr-heading"><div><h3>Harvest routing</h3><p>Cover costs → Harvest → Move, sell or add liquidity</p></div>${S.phase === 'after' || S.run?.phase === 'after' ? button('back-results', 'Back to results', false, S.busy) : button('back', 'Back to harvest', false, S.busy)}</div>
      ${S.busy ? '<p class="hm-statusline" role="status"><span class="spinner spinner-sm" aria-hidden="true"></span>Reading and preparing routing…</p>' : ''}
      ${S.data?.issues?.length ? `<p class="hr-error">${esc(S.data.issues.join(' · '))}</p>` : ''}
      ${!S.data ? button('refresh', 'Retry loading', false, S.busy) : S.run ? `${harvestRewardsHtml()}${receiptHtml()}${S.adopted ? '<p class="hm-hint">This run’s transaction is still being confirmed. Remaining steps will not be routed later.</p>' : ''}<section><p class="hr-scope">${S.run.scope.kind === 'regions' ? S.run.scope.region_uids.map(regionBadge).join(' ') : 'All regions'}</p><h3>${S.run.phase === 'before' ? 'Before harvest' : S.run.phase === 'harvest' ? 'Harvest' : 'After harvest'} · ${esc(STATE_LABELS[S.run.state] || S.run.state)}</h3>${(S.run.phase_steps?.before || []).map(stepHtml).join('')}${S.run.steps.map(stepHtml).join('')}${S.run.phase === 'harvest' && !harvestDone ? savedRulesHtml() : ''}${S.run.phase === 'harvest' ? `<p>${harvestDone ? 'Harvest phase finished. Only confirmed proceeds are available for routing.' : `Harvest inputs are covered for ${esc(scopeLabel(S.run.scope))}. The next action requests wallet approval to harvest this selection.`}</p>${(!harvestDone || S.error || S.editingRun) ? button(harvestDone ? 'preview-after' : 'harvest', harvestDone ? 'Review harvest allocations' : `Harvest ${esc(scopeLabel(S.run.scope))}`, true, S.busy) : ''}${!harvestDone && terminalAttempts && hasProceeds ? button('close-harvest', 'Route confirmed portion', false, S.busy) : ''}${attempts.some((item) => item.evidence?.tx_id) ? button('check-harvest', 'Refresh harvest confirmations', false, S.busy) : ''}${harvestDone ? button('edit-allocations', S.editingRun ? 'Hide allocation editor' : 'Edit allocations', false, S.busy) : ''}${S.editingRun ? rulesHtml() : ''}${harvestDone && S.editingRun && S.preview?.phase === 'after' ? `<div class="hr-steps">${S.preview.steps.map(stepHtml).join('')}</div>${button('start', 'Confirm allocation plan', true, S.busy || S.preview.blocked)}` : ''}` : ''}</section>

        ${fundingHtml(next)}<div class="hr-actions">${!S.adopted && next?.wallet_outcome === 'not_sent' ? button('retry-unsent', 'Review unsent step again', false, S.busy) : ''}${!S.adopted && failedStepSkippable(next, S.error) ? button('skip-failed', 'Skip failed step and continue', false, S.busy) : ''}${!S.adopted && next && ['planned', 'reviewed'].includes(next.state) && (!!S.error || S.run.state === 'failed') ? button('review', S.run.state === 'failed' ? 'Review next routing step' : 'Refresh quotes', true, S.busy) : ''}${next?.tx_id && (Date.now() >= pollDeadline || S.error) && ['submitted', 'pending', 'settling'].includes(next.state) ? button('verify', 'Retry confirmation', false, S.busy) : ''}${next?.state === 'settling' && next.bridge_next_start != null ? button('verify-older', 'Search earlier bridge activity', false, S.busy) : ''}${next?.state === 'awaiting_wallet' && !next.tx_id ? '<label class="hr-field"><span>Transaction ID from wallet history</span><input data-hr-recovery autocomplete="off" spellcheck="false"></label>' + button('recover', 'Check transaction', false, S.busy) + button('not-sent', 'Nothing was signed', false, S.busy) : ''}${!TERMINAL.has(S.run.state) ? button('cancel', 'Cancel remaining steps', false, S.busy || inFlight(S.run)) : button('done', 'Done', true, S.busy)}</div>`
      : S.phase === 'after' ? `<section class="hr-preflight"><h3>Route confirmed harvest</h3>${S.rules.length ? (S.error ? '<p>Harvest and routing are separate. Retry preparation to read the confirmed proceeds; this does not harvest again.</p>' : '<p>Review the confirmed allocations and choose the DEC source for every liquidity deposit.</p>') : '<p>No routing rules are saved for this selection.</p>'}${S.error ? button('prepare-receipt', 'Retry preparation', true, S.busy) : ''}${previewPlanHtml('Use these allocations')}<details><summary>Edit routing rules</summary>${rulesHtml()}${button('prepare-receipt', 'Apply routing rules', true, S.busy || !S.rules.length)}</details></section>` : `${reserveHtml()}${rulesHtml()}<section class="hr-preflight"><h3>${S.phase === 'before' ? 'Before harvest' : 'Confirmed harvest allocation'}</h3><p>${S.phase === 'before' ? 'Use donor regions first. Only the uncovered shortfall is bought with DEC. Each step is reviewed before signing.' : 'Fresh quotes use the confirmed receipts from this harvest.'}</p>${button('preview', S.phase === 'before' ? `Check harvest inputs · ${esc(scopeLabel(scope))}` : 'Review harvest routing', true, S.busy || S.data.blocked)}${S.preview ? `<div class="hr-steps">${S.preview.steps.length ? S.preview.steps.map(stepHtml).join('') : `<p class="hr-check-result" role="status">${S.phase === 'before' ? `Inputs ready for ${esc(scopeLabel(scope))}. No transfers or purchases are needed before harvest.` : 'No resource allocations are needed for this harvest.'}</p>`}${S.preview.blocked && S.preview.issues?.length ? `<p class="hr-error">${esc(S.preview.issues.join(' · '))}</p>` : ''}</div>${button('start', S.phase === 'before' ? (S.preview.steps.some(step => step.status !== 'empty') ? 'Review input transfers' : `Harvest ${esc(scopeLabel(scope))}`) : 'Use these allocations', true, S.busy || S.preview.blocked)}` : ''}</section>`}
      </section>`;
  }
  function change(target) {
    if (target.dataset.hrMember) {
      if (S.busy || !planSelectableIds().includes(target.dataset.hrMember)) return;
      if (target.checked) S.selectedMemberIds.add(target.dataset.hrMember);
      else S.selectedMemberIds.delete(target.dataset.hrMember);
      onRender();
      return;
    }
    if (target.dataset.hrReserve) { reserveChange(target); return; }
    if (target.dataset.hrDecSource !== undefined) {
      if (S.busy || (S.run && !S.editingRun)) return;
      const rule = S.rules.find((item) => item.id === target.dataset.rule);
      const value = target.dataset.hrDecSource;
      if (!rule || !['wallet', 'swap_half'].includes(value)) return;
      rule.dec_source = value;
      S.preview = null;
      try { save(); } catch (error) { S.error = error.message; onRender(); return; }
      work(async () => {
        S.preview = S.run
          ? await api('preview', { routing_run_id: S.run.run_id, rules: S.rules, slippage: S.slippage })
          : await api('preview', { scope, phase: S.phase, rules: S.rules, reserves: S.reserves, slippage: S.slippage, harvest_attempt_ids: receipts });
      });
      return;
    }
    const name = target.dataset.hrField;
    if (!name || S.busy || (S.run && !S.editingRun)) return;
    try {
      {
        const rule = S.rules.find((r) => r.id === target.dataset.rule);
        if (!rule) return;
        rule[name] = target.value;
        if (name === 'source_region') {
          const available = (S.data.pools || []).map((pool) => pool.symbol).filter((symbol) => rule.source_region === 'inventory' ? ['SPS', 'VOUCHER'].includes(symbol) : !['SPS', 'VOUCHER'].includes(symbol));
          if (!available.includes(rule.symbol)) rule.symbol = available[0] || '';
        }
      }
      S.preview = null; save();
      if (target.tagName === 'SELECT') onRender();
      else {
        const root = target.closest('.harvest-routing');
        root?.querySelector('.hr-steps')?.remove();
        root?.querySelector('[data-hr="start"]')?.remove();
      }
    } catch (error) { S.error = error.message; onRender(); }
  }
  async function action(name, element) {
    if (S.run?.routing_closed && !['done', 'back'].includes(name)) return;
    if (S.adopted && ['review', 'sign', 'skip-failed', 'retry-unsent', 'cancel'].includes(name)) return;
    if (name === 'done' && !S.busy) { clearTimeout(syncTimer); onDone(); return; }
    if (name === 'back' && !S.busy) { await stop(); onBack(); return; }
    if (name === 'back-results' && !S.busy) { clearTimeout(syncTimer); onBack(); return; }
    if (name === 'harvest' && !S.busy) { onHarvest(S.run); return; }
    await work(async () => {
      if (name === 'prepare-receipt') { await prepareReceiptRouting(); }
      else if (name === 'refresh') { S.data = await api('state'); const history = await api('runs'); selectSavedRun(history.runs); }
      else if (name === 'add-reserve' && !S.run) { S.reserves.push({ region_uid: 'inventory', symbol: 'DEC', amount: '0' }); S.preview = null; save(); }
      else if (name.startsWith('remove-reserve:') && !S.run) { S.reserves.splice(Number(name.slice(15)), 1); S.preview = null; save(); }
      else if (name === 'add') {
        const first = (S.data.regions || []).find((r) => scope.kind !== 'regions' || scope.region_uids.includes(r.region_uid));
        if (!first) throw new Error('Choose a region to harvest first.');
        S.rules.push({ id: crypto.randomUUID(), kind: 'sell', source_region: first.region_uid, symbol: 'GRAIN', quantity_mode: 'percent', quantity: '25', destination_symbol: 'DEC', destination_region: first.region_uid, slippage: '2.5' });
        S.preview = null; save();
      } else if (name.startsWith('remove:')) { S.rules = S.rules.filter((r) => r.id !== name.slice(7)); S.preview = null; save(); }
      else if (name === 'preview') { S.preview = await api('preview', { scope, phase: S.phase, rules: S.rules, reserves: S.reserves, slippage: S.slippage, harvest_attempt_ids: receipts }); }
      else if (name === 'preview-after') S.preview = await api('preview', { routing_run_id: S.run.run_id, ...(S.editingRun ? { rules: S.rules, slippage: S.slippage } : {}) });
      else if (name === 'edit-allocations') {
        if (!S.editingRun) { S.rules = structuredClone(S.run.rules); S.slippage = S.run.slippage; }
        S.editingRun = !S.editingRun; S.preview = null;
      }
      else if (name === 'close-harvest') { S.run = await api('close-harvest', { run_id: S.run.run_id }); S.preview = null; }
      else if (name === 'check-harvest') {
        for (const item of S.run.harvest_attempt_records || []) {
          if (item.evidence?.tx_id) await api('verify', { attempt_id: item.attempt_id }, '/api/harvest/');
        }
        const history = await api('runs'); S.run = history.runs.find((run) => run.run_id === S.run.run_id);
      }
      else if (name === 'start') {
        if (!S.preview || (S.run && S.preview.phase !== 'after')) throw new Error('Review the current routing plan before continuing.');
        if (Array.isArray(S.preview.rules)) { S.rules = structuredClone(S.preview.rules); save(); }
        S.run = await api('run', { preview_id: S.preview.preview_id, preview_digest: S.preview.preview_digest });
        routingClientRunId(player, S.run.run_id);
        S.selectionInitialized = false;
        initializePlanSelection();
        S.preview = null; setReview(null); S.editingRun = false;
      }
      else if (name === 'review') { const result = await api('review', { run_id: S.run.run_id }); S.run = result.run; setReview(result.review, true); }
      else if (name === 'retry-unsent') { S.run = await api('retry-unsent', { run_id: S.run.run_id }); setReview(null); }
      else if (name === 'skip-failed') {
        const target = S.run.steps.find(step => step.state === 'failed')
          || S.run.steps.find(step => !['confirmed', 'skipped'].includes(step.state) && step.depends_on_step_id);
        S.run = await api('skip-failed', { run_id: S.run.run_id, step_id: target?.step_id }); setReview(null);
      }
      else if (name === 'cancel') { S.run = await api('cancel', { run_id: S.run.run_id }); setReview(null); }
      else if (name === 'verify' || name === 'verify-older') { S.run = await api('verify', { run_id: S.run.run_id, older_bridge_history: name === 'verify-older' }); syncReviewFromRun(); }
      else if (name.startsWith('funding-')) {
        const step = S.run.steps.find((item) => item.state === 'settling');
        if (!step) throw new Error('No SPS operation is awaiting settlement.');
        const runId = S.run.run_id;
        const fundingRequest = (action, extra = {}) => api('funding', { action, run_id: runId, step_id: step.step_id, ...extra });
        if (name === 'funding-review') S.run = await fundingRequest('review');
        else if (name === 'funding-verify') S.run = await fundingRequest('verify');
        else if (name === 'funding-adopt') {
          const tx = element.closest('.harvest-routing').querySelector('[data-hr-confirmed-funding]').value.trim();
          S.run = await fundingRequest('recover-confirmed', { tx_id: tx });
        }
        else if (name === 'funding-recover') {
          const tx = element.closest('.harvest-routing').querySelector('[data-hr-funding-recovery]').value.trim();
          S.run = await fundingRequest('transaction', { tx_id: tx });
          S.run = await fundingRequest('verify');
        } else if (name === 'funding-sign') await signFundingStep(step);
      }
      else if (name === 'recover') {
        const next = S.run.steps.find((step) => step.state === 'awaiting_wallet');
        const tx = element.closest('.harvest-routing').querySelector('[data-hr-recovery]').value.trim();
        S.run = await api('transaction', { run_id: S.run.run_id, step_id: next.step_id, tx_id: tx });
        S.run = await api('verify', { run_id: S.run.run_id });
        syncReviewFromRun();
      } else if (name === 'not-sent') {
        const next = S.run.steps.find((step) => step.state === 'awaiting_wallet');
        S.run = await api('not-sent', { run_id: S.run.run_id, step_id: next.step_id, broadcasted: false });
        setReview(null);
      } else if (name === 'release') {
        const child = releasableDependency();
        const dependency = child?.dependsOn || child?.releasedDependency;
        if (!dependency) throw new Error('No blocked signed transaction can be released.');
        const next = S.run.steps.find(step => step.tx_id === child.txId);
        const result = await releaseSignedQueueDependency(player, child.txId, dependency);
        if (result.notSent) {
          S.run = await api('release-unsent', { run_id: S.run.run_id, step_id: next.step_id,
            tx_id: result.txId, depends_on: result.dependsOn, queue_state: result.state });
        } else {
          S.chainActive = true;
        }
      } else if (name === 'sign') {
        if (!isCurrent() || !walletReady(player)) throw new Error('Reconnect the selected wallet before signing.');
        S.chainActive = true;
        const runId = S.run.run_id;
        const firstReview = S.review;
        const firstMemberIds = selectedReviewIds(firstReview);
        const firstStepIndex = S.run.steps.findIndex(step => reviewMemberIds(firstReview).includes(step.step_id));
        let previousTxId = S.run.steps.slice(0, Math.max(0, firstStepIndex)).reverse().find(step => step.tx_id)?.tx_id || null;
        const chain = createRoutingSignatureChain({
          getId: item => item.envelope?.txId || item.handoff.step_id,
          onStepChange: update => {
            S.signatureUpdates = [...S.signatureUpdates.filter(item => item.id !== update.id), update];
            if (isCurrent()) onRender();
          },
          sign: async item => {
            if (!isCurrent() || !walletReady(player)) {
              S.run = await api('not-sent', { run_id: runId, step_id: item.handoff.step_id, broadcasted: false });
              throw new Error('The player or wallet changed before signing. Nothing was sent.');
            }
            try {
              const result = await requestSignatureThroughGate(player, item.handoff.operations, null, item.handoff.authority, item.envelope);
              item.txId = result.txId;
              try {
                S.run = await api('transaction', { run_id: runId, step_id: item.handoff.step_id, tx_id: result.txId });
              } catch (error) {
                error.txId = result.txId;
                error.signedResult = result;
                throw error;
              }
              S.review = null;
              return result;
            } catch (error) {
              const txId = error.txId || null;
              if (!txId && error.broadcasted === false) {
                S.run = await api('not-sent', { run_id: runId, step_id: item.handoff.step_id, broadcasted: false });
              }
              throw error;
            }
          },
        });
        async function* signingRequests() {
          let review = firstReview;
          // each reviewed sale bundle is filtered through the plan-wide selection
          // dependent and other non-selectable approvals remain whole.
          let memberIds = firstMemberIds;
          while (review && isCurrent()) {
            const handoff = await api('handoff', { run_id: runId, review_digest: review.digest, member_ids: memberIds });
            if (handoff.skipped) {
              S.run = handoff.run;
              const result = await api('review', { run_id: runId });
              S.run = result.run; setReview(result.review, true);
              review = result.review;
              memberIds = selectedReviewIds(review);
              continue;
            }
            // This begins only after `/handoff` durably recorded awaiting_wallet. It can prepare
            // an independent next sale while the current wallet request is open; the next
            // `/handoff` still performs the live server-side freshness check before it signs.
            const prefetchedReview = api('prefetch', { run_id: runId, step_id: handoff.step_id })
              .catch(error => ({ error }));
            let envelope;
            try {
              envelope = (await prepareSigningBatch(player, [handoff.operations], handoff.authority,
                { runId: routingClientRunId(player, runId), runKind: 'harvest', maxOpsPerTx: maxOpsPerTx(S.data) }))?.[0] || null;
            } catch (error) {
              S.run = await api('not-sent', { run_id: runId, step_id: handoff.step_id, broadcasted: false });
              throw error;
            }
            if (envelope && previousTxId) envelope.dependsOn = previousTxId;
            const item = { handoff, envelope, memberIds: review.member_ids || [handoff.step_id] };
            yield item;
            previousTxId = item.txId;
            if (!isCurrent()) return;
            const prepared = await prefetchedReview;
            if (prepared.error) throw new Error(`The next routing step could not be prepared safely: ${prepared.error.message}`);
            if (prepared.prefetched) {
              if (!prepared.review) throw new Error('The next routing step could not be prepared safely. No transaction was sent.');
              setReview(prepared.review, true);
              review = prepared.review;
            } else {
              const result = await api('review', { run_id: runId });
              S.run = result.run; setReview(result.review, true);
              review = result.review;
            }
            memberIds = selectedReviewIds(review);
          }
        }
        await chain.run(signingRequests());
        const history = await api('runs');
        S.run = history.runs.find((run) => run.run_id === runId) || S.run;
        setReview(null);
        if (TERMINAL.has(S.run.state)) S.chainActive = false;
      }
    });
    if (name === 'sign' && isCurrent() && S.run?.state === 'ready_harvest' && !S.error) { onHarvest(S.run); return; }
    if (name === 'start' && isCurrent() && S.run?.state === 'ready_harvest' && !S.error) { onHarvest(S.run); return; }
    if (name === 'preview' && isCurrent()) document.querySelector('.harvest-routing .hr-preflight .hr-steps')?.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
  async function load() {
    await work(async () => {
      const [data, history] = await Promise.all([api('state'), api('runs')]);
      S.data = data;
      selectSavedRun(history.runs);
      if (S.run) { S.phase = S.run.phase; syncReviewFromRun(); }
      const attempts = S.run?.harvest_attempt_records || [];
      const completedHarvest = S.run?.phase === 'harvest' && attempts.length === S.run.harvest_preview?.chunks?.length && attempts.length > 0
        && attempts.every(({ evidence }) => evidence?.tx_id && evidence.members?.length && evidence.members.every(member => member.outcome === 'success' && member.game_state === 'accepted' && member.evidence?.length));
      if (completedHarvest) S.preview = await api('preview', { routing_run_id: S.run.run_id });
      else if (!S.run && !S.otherRun && receipts.length) await prepareReceiptRouting();
    });
  }
  return { html, footerHtml, change, action, load, stop, clear, get busy() { return S.busy; },
    get runId() { return S.run?.run_id || null; }, get terminal() { return S.run ? !!S.run.routing_closed || TERMINAL.has(S.run.state) : !activeRunId; } };
}
