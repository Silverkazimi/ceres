import { balancesChanged } from './balances-bus.js';
import { recordChainUnstake } from './stage-plans.js';
import { COMMIT_ICON } from './button-icons.js';
import { walletSignLabelHtml, walletReady } from './wallet.js';
import { BOARD, CURRENT_PLAYER, adoptLatestCapture, NOTICE_CATALOG, catalogNotice, noticeMessage, playerQueryAppend, renderNotices } from './board.js';
import { recoverPendingBroadcastRequests, requestBroadcastThroughGate, requestSignatureThroughGate, prepareSigningBatch } from './broadcast-gate.js';
import { waitForQueuedSubmission, resumeSignedQueue } from './transaction-queue.js';
import { normalizeEstimate } from './candidates.js';
import { checkoutSignableEntries, renderCheckoutSurface } from './checkout.js';
import { esc, formatPlotLabel, humanizeInstant } from './format.js';
import { refreshChangeBadges, updateMountedRow } from './rows.js';
import { CERES_PORTFOLIO_KEY, CERES_STAGE, CERES_STAGED_WORKSITES, cloneStageAsset, restoreStagedMove, stageMovePayload, stageToast, stagedEntriesForRow } from './staging.js';
import { refreshConfirmedPlot } from './worksite.js';
import { S } from './state.js';

export const CERES_PREVIEWS = new Map();
// one auto-resolve loop per surface, no matter how often Sign is pressed.
const CERES_AUTO_RESOLVING = new Set();
export const FINISH_TRANSACTION_FIRST = 'Finish the current transaction first';
let CERES_CONFIRMATION_BANNER = null;
// a batch of several transactions keeps one line per transaction in the pill; null for a single one.
let CERES_BATCH = null;
export let CERES_BROADCAST_ACTIVE = 0;
// the Receipts list this browser kept per player (`ceres:receipts:v1:<account>`) is removed once, silently.
try {
  for (let i = localStorage.length - 1; i >= 0; i -= 1) {
    const name = localStorage.key(i);
    if (name && name.startsWith('ceres:receipts:v1:')) localStorage.removeItem(name);
  }
} catch (_) { /* storage unavailable: nothing to remove */ }
export const OPERATION_RECEIPTS = new Map();
export const OPERATION_POLL_SCHEDULE_MS = window.__ceresOperationPollSchedule || [3000, 8000, 15000, 30000, 60000, 120000];
export const OPERATION_POLLERS = new Map();
// All Ceres transaction receipts use Hivehub ().
export const HIVE_TX_EXPLORER = (txId) => `https://hivehub.dev/tx/${encodeURIComponent(txId)}`;

function clearPendingReview(review) {
  review.broadcastPending = false;
  delete review.pendingBroadcastIds;
}

window.addEventListener('ceres:broadcast-request-released', (event) => {
  const released = new Set(event.detail?.identities || []);
  for (const [key, review] of CERES_PREVIEWS.entries()) {
    if (!(review?.pendingBroadcastIds || []).some((identity) => released.has(identity))) continue;
    clearPendingReview(review);
    if (key === CERES_PORTFOLIO_KEY) renderCheckoutSurface();
    else updateMountedRow(key);
  }
});

// the page-level auto-resolve settles earlier requests on its own schedule.
// Without this, a surface that stopped waiting would sit on a disabled Sign under the
// words "this clears itself" long after the request was actually cleared, and only a
// reload would recover it -- the very trap the ticket exists to remove.
window.addEventListener('ceres:pending-requests-resolved', () => {
  for (const [key, review] of CERES_PREVIEWS.entries()) {
    if (!review?.pendingWalletBlock) continue;
    delete review.pendingWalletBlock;
    clearPendingReview(review);
    if (key === CERES_PORTFOLIO_KEY) renderCheckoutSurface();
    else updateMountedRow(key);
  }
});

// reload-proof marker; lift with the ceres-modularisation-campaign block.
const IN_FLIGHT_RECORD_TTL_MS = 24 * 60 * 60 * 1000;
let IN_FLIGHT_STORE_FAILED = false;

export function inFlightStoreKey() {
  const account = (CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  return account ? `ceres:in-flight:v1:${account}` : null;
}

function inFlightStoreFailure() {
  if (IN_FLIGHT_STORE_FAILED) return;
  IN_FLIGHT_STORE_FAILED = true;
  stageToast(noticeMessage(catalogNotice('in_flight_store_unavailable')), true);
}

function readInFlightRecords() {
  const key = inFlightStoreKey();
  if (!key) return { records: [], expired: [] };
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { records: [], expired: [] };
    const parsed = JSON.parse(raw);
    const records = Object.values(parsed?.records || {}).filter((record) => record?.txId && Array.isArray(record?.operations));
    const expired = records.filter((record) => Date.now() - Date.parse(record?.broadcast_at || record?.queued_at) > IN_FLIGHT_RECORD_TTL_MS);
    const live = records.filter((record) => !expired.includes(record));
    if (expired.length) {
      try {
        // (audit A16): an old record leaves the live list but is never destroyed by time; it is kept beside it as evidence.
        const archiveKey = `${key}:expired`;
        const kept = JSON.parse(localStorage.getItem(archiveKey) || '{}') || {};
        expired.forEach((record) => { kept[record.txId] = record; });
        localStorage.setItem(archiveKey, JSON.stringify(Object.fromEntries(Object.entries(kept).slice(-200))));
        localStorage.setItem(key, JSON.stringify({ version: 1, records: Object.fromEntries(live.map((record) => [record.txId, record])) }));
      } catch (error) {
        inFlightStoreFailure();
      }
    }
    return { records: live, expired };
  } catch (error) {
    return { records: [], expired: [] };
  }
}

function writeInFlightRecord(record) {
  const key = inFlightStoreKey();
  if (!key || !record?.txId) return;
  try {
    const current = readInFlightRecords().records;
    const existing = current.find((item) => item?.txId === record.txId);
    const merged = {
      ...(existing || {}),
      ...record,
      broadcast_at: existing?.broadcast_at || record.broadcast_at,
      entryId: existing && Object.prototype.hasOwnProperty.call(existing, 'entryId') ? existing.entryId : record.entryId,
    };
    const records = Object.fromEntries(current.filter((item) => item?.txId !== record.txId).concat(merged).map((item) => [item.txId, item]));
    localStorage.setItem(key, JSON.stringify({ version: 1, records }));
  } catch (error) {
    inFlightStoreFailure();
  }
}

// an orange move's unstake (built by checkoutSignableEntries) finds its staged stake again by value.
function restakeParent(restake) {
  return restake ? CERES_STAGE.find((entry) => entry.deed_uid === restake.target_deed_uid && entry.asset_ref === restake.asset_ref && entry.action !== 'unstake') || null : null;
}
// Owner ruling (): "Cooling view remembers" -- once the unstake is confirmed the planned restake (card, target plot
// slot) is kept per account for the Cooling view (R-19) to offer when the card is ready; unsigned stage never survives a reload.
export function plannedRestakeKey() {
  const account = (CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
  return account ? `ceres:planned-restake:v1:${account}` : null;
}
export function readPlannedRestakes() {
  const key = plannedRestakeKey();
  if (!key) return {};
  try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (error) { return {}; }
}
function savePlannedRestake(restake, txId) {
  const key = plannedRestakeKey();
  if (!key || !restake?.asset_ref) return;
  try {
    const all = readPlannedRestakes();
    all[restake.asset_ref] = { ...restake, unstake_tx: txId || null, saved_at: new Date().toISOString() };
    localStorage.setItem(key, JSON.stringify(all));
  } catch (error) { /* storage blocked: the Cooling view simply offers no remembered plot */ }
}

// phase 4 (K4): once an orange card's unstake is confirmed its stake STAYS in the target tile as a plan -- red
// because the card now cools down. The next load's live verdict puts in the real end of the cooldown (never start+72h).
// Without the staged stake (e.g. a confirmation after a reload), the restake is kept for the plan store to pick up.
export function applyConfirmedRestake(entry) {
  const parent = restakeParent(entry?._restake);
  if (!parent) { savePlannedRestake(entry?._restake, entry?.txId); return null; }
  const cooldown = { ready: false, state: 'cooling', ready_at: null, reason: 'unstake confirmed — its cooldown end appears on the next read', unstaked_at: new Date().toISOString() };
  parent._unstake_tx = entry.txId || parent._unstake_tx || null;
  parent._source_deed_uid = null;
  parent.cooldown = cooldown;
  if (parent._asset) parent._asset = { ...parent._asset, cooldown };
  return parent;
}

function opKey(entry) {
  return `${entry?.deed_uid || ''}|${entry?.group_id || ''}|${entry?.slot_index || ''}|${entry?.action || ''}|${entry?.asset_ref || ''}`;
}

function stripForStore(entry) {
  return {
    ...entry,
    _asset: cloneStageAsset(entry?._asset),
    _previous_asset: cloneStageAsset(entry?._previous_asset),
  };
}

function forgetInFlightRecord(txId, { removeStage = true } = {}) {
  if (!txId) return;
  const key = inFlightStoreKey();
  if (key) {
    try {
      const records = readInFlightRecords().records.filter((record) => record?.txId !== txId);
      localStorage.setItem(key, JSON.stringify({ version: 1, records: Object.fromEntries(records.map((record) => [record.txId, record])) }));
    } catch (error) {
      inFlightStoreFailure();
    }
  }
  if (!removeStage) return;
  const affectedDeeds = new Set();
  for (let index = CERES_STAGE.length - 1; index >= 0; index -= 1) {
    const entry = CERES_STAGE[index];
    if (entry?.txId !== txId) continue;
    affectedDeeds.add(entry.deed_uid);
    CERES_STAGE.splice(index, 1);
  }
  affectedDeeds.forEach((deedUid) => updateMountedRow(deedUid));
  if (affectedDeeds.size) renderCheckoutSurface();
}

export function inFlightRecordForEntry(entry) {
  const key = opKey(entry);
  return readInFlightRecords().records.find((record) => record?.entries?.some((storedEntry) => opKey(storedEntry) === key)) || null;
}

export function restoreInFlightRecords() {
  const { records, expired } = readInFlightRecords();
  if (expired.length) setConfirmationBannerState(noticeMessage(catalogNotice('in_flight_record_expired')), '', 'caution', { state: 'is-unresolved', aria: 'assertive' });
  let restored = false;
  for (const record of records) {
    const restoredEntries = [];
    const restoredWorksites = [];
    for (const storedEntry of record?.entries || []) {
      if (CERES_STAGE.some((entry) => opKey(entry) === opKey(storedEntry))) continue;
      const entry = {
        ...storedEntry,
        base: normalizeEstimate(storedEntry?.base),
        _asset: cloneStageAsset(storedEntry?._asset),
        _previous_asset: cloneStageAsset(storedEntry?._previous_asset),
        txId: storedEntry?.txId,
        broadcast_at: storedEntry?.broadcast_at,
      };
      CERES_STAGE.push(entry);
      restoredEntries.push(entry);
      CERES_PREVIEWS.delete(entry.deed_uid);
    }
    for (const storedBuild of record?.worksites || []) {
      if (!storedBuild?.deed_uid || !storedBuild.target) continue;
      const build = { ...storedBuild, txId: record.txId, broadcast_at: record.broadcast_at };
      CERES_STAGED_WORKSITES.set(build.deed_uid, build);
      restoredWorksites.push(build);
      CERES_PREVIEWS.delete(build.deed_uid);
    }
    if (!restoredEntries.length && !restoredWorksites.length) continue;
    restored = true;
    const chunk = { operations: record.operations, entryId: record.entryId, runId: record.runId, runKind: record.runKind };
    const review = {
      scope: record.scope,
      runId: record.runId,
      runKind: record.runKind,
      payload: { account: record.account, chunks: [chunk] },
      stagedSnapshot: restoredEntries,
      stagedWorksitesSnapshot: restoredWorksites,
      restored: true,
    };
    CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
    if (record.queued_at && !record.broadcast_at) {
      resumeSignedQueue(record.account);
      waitForQueuedSubmission(record.account, record.txId).then(() => {
        markBroadcastedChunk(review, chunk, record.txId);
        return verifyBroadcast(chunk, null, record.txId, review);
      }).catch(error => {
        if (error.broadcasted === false) markGameRefusedChunk(review, chunk, record.txId, error.message);
      });
    } else verifyBroadcast(chunk, null, record.txId, review).catch(() => {});
  }
  if (restored) {
    setConfirmationBannerState(noticeMessage(catalogNotice('verification_in_flight_restored')), '', 'info', { state: 'is-sent', txId: records[0]?.txId, aria: 'polite' });
    renderCheckoutSurface();
  }
}

// Item 19 -- renders `/op-preview`'s `hazards` array (backend, op_preview.py
// already landed by another agent) as plain visible text, no gate. the owner's
// ruling, live testing 2026-08-24: "A warning is ok but never a blocker like
// this" -- the sole-power-source-removal REFUSAL was fixed backend-side; this
// is the frontend half, just rendering `response.hazards[].message`. NOT the
// same thing as `powerWarning` just below (a client-side heuristic computed
// the instant a power-core unstake is staged, before any server round trip)
// this one is the SERVER's own authoritative check (it additionally
// suppresses the warning when the plot already has 0 workers/0 PP, which the
// client heuristic does not know to do), and only exists once a preview has
// actually been fetched. Empty/missing hazards renders nothing -- no reserved
// space, per your instruction.
export function serverNoticeHtml(notices, hazards = []) {
  const source = Array.isArray(notices) && notices.length ? notices : (hazards || []).map((hazard) => ({
    code: hazard.type || 'unknown_notice', kind: hazard.type === 'workers_staged_without_power' ? 'warning' : 'hazard',
    severity: hazard.type === 'workers_staged_without_power' ? 'caution' : 'danger', blocking: hazard.type === 'workers_staged_without_power' ? 'none' : 'confirm',
    message: hazard.message, reason: hazard.message, context: { deed_uid: hazard.deed_uid },
  }));
  return renderNotices(source, 'slot-hazard-inline');
}

function previewKey(target) {
  return target?.scope === 'portfolio' ? CERES_PORTFOLIO_KEY : target?.deed_uid;
}

export function renderOperationReview(target, review, opts = {}) {
  const portfolio = target?.scope === 'portfolio';
  if (opts.compact) return compactOperationReview(target, review, opts);
  if (review.loading) return `<section class="op-preview-review"><em>${review.refreshing ? 'The board just updated — preparing the preview on the fresh data…' : 'Building the current preview…'}</em></section>`;
  if (review.error) return `<section class="op-preview-review is-error"><strong>Preview refused:</strong> ${esc(review.error)}${renderNotices(review.errorNotices, 'op-state-notice')}</section>`;
  const payload = review.payload;
  const chunks = payload?.chunks || [];
  const operationDetails = (operation) => {
    const body = operation?.[1] || {};
    let parsed = {};
    try { parsed = JSON.parse(body.json || '{}'); } catch (error) { parsed = {}; }
    const actions = ['unstake', 'stake'].flatMap((action) => {
      const section = parsed[action] || {};
      const cards = Array.isArray(section.cards) ? section.cards.length : 0;
      const items = Array.isArray(section.items) ? section.items.length : 0;
      const total = cards + items;
      if (!total) return [];
      const parts = [];
      if (cards) parts.push(`${cards} card${cards === 1 ? '' : 's'}`);
      if (items) parts.push(`${items} item${items === 1 ? '' : 's'}`);
      return [`${action === 'stake' ? 'Stake' : 'Unstake'} ${parts.join(' and ')}`];
    });
    const active = Array.isArray(body.required_auths) && body.required_auths.length;
    const posting = Array.isArray(body.required_posting_auths) && body.required_posting_auths.length;
    const auth = [active ? 'Active key' : '', posting ? 'Posting key' : ''].filter(Boolean).join(' + ') || 'No account key';
    return { meaning: actions.join(' · ') || 'Land operation', auth };
  };
  // LOCAL INVARIANT: every operation keeps its meaning and signing tier outside raw-data disclosure.
  const chunkHtml = chunks.map((chunk, chunkIndex) => {
    const operations = Array.isArray(chunk.operations) ? chunk.operations : [];
    const rows = operations.map((operation, operationIndex) => {
      const detail = operationDetails(operation);
      return `<li class="op-review-op"><span class="op-review-op-number">Operation ${operationIndex + 1}</span><span class="op-review-op-meaning">${esc(detail.meaning)}</span><span class="op-review-auth">Auth: ${esc(detail.auth)}</span></li>`;
    }).join('');
    const count = operations.length;
    return `<section class="op-review-chunk"><header><strong>Transaction ${chunkIndex + 1} of ${chunks.length}</strong><span>${count} operation${count === 1 ? '' : 's'}</span></header><ol class="op-review-ops">${rows}</ol></section>`;
  }).join('');
  const raw = JSON.stringify(chunks.map((chunk) => chunk.operations), null, 2);
  const states = chunks.map((chunk, index) => `<div class="op-state" data-op-state="${index}">pending — not broadcast</div>`).join('');
  const warning = chunks.length > 1
    ? `<p class="checkout-chunk-warning">This will require ${chunks.length} separate Keychain confirmations.</p>` : '';
  const targetAttr = portfolio ? 'data-preview-scope="portfolio"' : `data-deed-uid="${esc(target.deed_uid)}"`;
  const inFlight = review.broadcastPending || (review.stagedSnapshot || []).some((entry) => entry.txId && (entry.broadcast_at || entry.queued_at));
  const inFlightHtml = inFlight ? `<p class="op-preview-note">${esc(noticeMessage(catalogNotice('verification_in_flight')))}</p>` : '';
  const refreshedHtml = review.refreshedNotice ? '<p class="op-preview-note">The board updated since you last reviewed and these operations changed. Review them and confirm again.</p>' : '';
  return `<section class="op-preview-review"><h4>Review exact Hive operations</h4><p>${chunks.length} transaction${chunks.length === 1 ? '' : 's'} · Posting key · ${esc(payload.account)}</p>${warning}${refreshedHtml}${inFlightHtml}<div class="op-review-chunks">${chunkHtml}</div><details class="op-review-ops"><summary>Show transaction data</summary><pre>${esc(raw)}</pre></details><button type="button" class="slot-review ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="commit" data-op-action="confirm" ${targetAttr}${inFlight ? ' disabled' : ''}>${COMMIT_ICON}${walletSignLabelHtml('Confirm and sign with')}</button><div class="op-states">${states}</div></section>`;
}

// one-step checkout -- the preview is built automatically and folded into the panel
// one "Sign with Keychain" button, the exact Hive operations one click away.
function operationSummary(operation) {
  const body = operation?.[1] || {};
  let parsed = {};
  try { parsed = JSON.parse(body.json || '{}'); } catch (error) { parsed = {}; }
  const parts = ['unstake', 'stake'].flatMap((action) => {
    const section = parsed[action] || {};
    const cards = Array.isArray(section.cards) ? section.cards.length : 0;
    const items = Array.isArray(section.items) ? section.items.length : 0;
    const bits = [cards ? `${cards} card${cards === 1 ? '' : 's'}` : '', items ? `${items} item${items === 1 ? '' : 's'}` : ''].filter(Boolean);
    return bits.length ? [`${action === 'stake' ? 'Stake' : 'Unstake'} ${bits.join(' and ')}`] : [];
  });
  const active = Array.isArray(body.required_auths) && body.required_auths.length;
  const posting = Array.isArray(body.required_posting_auths) && body.required_posting_auths.length;
  // an operation that stakes nothing (a worksite change) is named by its own `op` field, plainly.
  const opName = typeof parsed.op === 'string' && parsed.op ? parsed.op.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()) : 'Land operation';
  return { meaning: parts.join(' · ') || opName, auth: [active ? 'Active key' : '', posting ? 'Posting key' : ''].filter(Boolean).join(' + ') || 'No account key' };
}

// phase 3: the Changes panel shows ONE Sign button, in its fixed footer below the whole plan -- the review
// then renders without its own (opts.footerSign) and the footer asks for this one.
export function operationSignButtonHtml(target, review, { surface = 'inline', iconHtml = COMMIT_ICON } = {}) {
  const worksite = surface === 'worksite' || surface === 'worksite-completion';
  const modal = worksite || surface === 'changes';
  const confirmAction = worksite ? 'data-worksite-action="broadcast"' : 'data-op-action="confirm"';
  const retryAction = worksite ? `data-worksite-action="${surface === 'worksite-completion' ? 'complete' : 'confirm'}"` : 'data-checkout-action="prepare"';
  const label = surface === 'worksite-completion' ? walletSignLabelHtml('Pay Grain and sign with') : walletSignLabelHtml();
  const keychainReady = walletReady();
  const targetAttr = target?.scope === 'portfolio' ? 'data-preview-scope="portfolio"' : `data-deed-uid="${esc(target?.deed_uid)}"`;
  // no recovery control. Ceres checks the earlier request itself; the button
  // stays a disabled Sign so the only thing he ever does here is sign.
  // the class strings sit beside their <button> so the button-contract scanner
  // (tests/ceres/test_fr5064_button_contract.py) can see them; the markup is unchanged.
  const primaryClass = modal ? `hm-btn is-primary ceres-btn ceres-btn--primary${worksite ? ' worksite-modal-confirm' : ''}` : 'op-sign-btn ceres-btn ceres-btn--primary';
  if (review?.pendingWalletBlock) return `<button type="button" class="${primaryClass}" data-control-density="comfortable" data-control-case="commit" ${confirmAction} ${targetAttr} disabled>${iconHtml ? `${iconHtml} ` : ''}${label}</button>`;
  const retryClass = modal ? `hm-btn is-secondary ceres-btn ceres-btn--secondary${worksite ? ' worksite-modal-confirm' : ''}` : 'op-sign-btn is-secondary ceres-btn ceres-btn--secondary';
  if (review?.error) return `<button type="button" class="${retryClass}" data-control-density="comfortable" ${retryAction}>Try again</button>`;
  const ready = review && !review.loading && review.payload && (review.payload.chunks || []).length > 0;
  const inFlight = review?.broadcastPending || (review?.stagedSnapshot || []).some((entry) => entry.txId && (entry.broadcast_at || entry.queued_at));
  const busy = CERES_BROADCAST_ACTIVE > 0 || review?.broadcasting || review?.broadcastPending;
  return `<button type="button" class="${primaryClass}" data-control-density="comfortable" data-control-case="commit" ${confirmAction} ${targetAttr}${!ready || inFlight || busy || !keychainReady ? ' disabled' : ''}>${iconHtml ? `${iconHtml} ` : ''}${label}</button>`;
}

function compactOperationReview(target, review, opts = {}) {
  const keychainReady = walletReady();
  const targetAttr = target?.scope === 'portfolio' ? 'data-preview-scope="portfolio"' : `data-deed-uid="${esc(target?.deed_uid)}"`;
  const button = (label, disabled, extra = '') => (opts.footerSign ? '' : `<button type="button" class="op-sign-btn ceres-btn ceres-btn--primary" data-control-density="comfortable" data-control-case="commit" data-op-action="confirm" ${targetAttr}${disabled ? ' disabled' : ''}${extra}>${COMMIT_ICON}${label}</button>`);
  if (!review || review.loading) {
    const text = review?.refreshing ? 'The board just updated — preparing on the fresh data…' : 'Preparing the transaction…';
    return `<section class="op-preview-review is-compact" aria-busy="true"><div class="op-sign-row"><div class="op-sign-summary"><span class="spinner" aria-hidden="true"></span><span>${text}</span></div>${button(walletSignLabelHtml(), true)}</div></section>`;
  }
  if (review.error) {
    return `<section class="op-preview-review is-compact is-error"><div class="op-sign-row"><div class="op-sign-summary"><strong>Cannot sign yet</strong><span>${esc(review.error)}</span></div>${opts.footerSign ? '' : '<button type="button" class="op-sign-btn is-secondary ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-checkout-action="prepare">Try again</button>'}</div>${renderNotices(review.errorNotices, 'op-state-notice')}</section>`;
  }
  const payload = review.payload;
  const chunks = payload?.chunks || [];
  const opCount = chunks.reduce((sum, chunk) => sum + (Array.isArray(chunk.operations) ? chunk.operations.length : 0), 0);
  const inFlight = review.broadcastPending || (review.stagedSnapshot || []).some((entry) => entry.txId && (entry.broadcast_at || entry.queued_at));
  const ops = chunks.map((chunk, chunkIndex) => (chunk.operations || []).map((operation) => {
    const d = operationSummary(operation);
    return `<li><span class="op-c-tx">${chunks.length > 1 ? `Tx ${chunkIndex + 1}` : ''}</span><span class="op-c-meaning">${esc(d.meaning)}</span><span class="op-c-auth">${esc(d.auth)}</span></li>`;
  }).join('')).join('');
  const raw = JSON.stringify(chunks.map((chunk) => chunk.operations), null, 2);
  const notes = [
    chunks.length > 1 && !opts.checkoutSummary ? `<p class="op-c-note is-warn">${chunks.length} wallet confirmations — one per transaction.</p>` : '',
    review.refreshedNotice ? '<p class="op-c-note is-warn">The board updated and these operations changed — check them before signing.</p>' : '',
    review.pendingWalletBlock ? `<p class="op-c-note is-bad" role="alert">${esc(review.pendingWalletBlock.message)}</p>` : '',
    inFlight ? `<p class="op-c-note">${esc(noticeMessage(catalogNotice('verification_in_flight')))}</p>` : '',
    keychainReady || opts.checkoutSummary ? '' : '<p class="op-c-note is-bad">Wallet not connected.</p>',
  ].join('');
  const states = chunks.map((chunk, index) => `<div class="op-state" data-op-state="${index}"></div>`).join('');
  if (opts.worksiteCompletion) {
    return `<section class="op-preview-review is-compact"><p class="ws-signing-account">Account: ${esc(payload.account)}</p>${notes}<details class="op-c-details"><summary>Transaction data</summary><pre>${esc(raw)}</pre></details><div class="op-states">${states}</div></section>`;
  }
  return `<section class="op-preview-review is-compact">${opts.checkoutSummary ? '' : `<div class="op-sign-row"><div class="op-sign-summary"><strong>${opCount} operation${opCount === 1 ? '' : 's'} · ${chunks.length} transaction${chunks.length === 1 ? '' : 's'}</strong><span>Posting key · ${esc(payload.account)}</span></div>${button(walletSignLabelHtml(), inFlight || !keychainReady)}</div>`}${notes}<details class="op-c-details"><summary>Show Hive operations</summary><ol class="op-c-ops">${ops}</ol><details class="op-c-raw"><summary>Raw transaction data</summary><pre>${esc(raw)}</pre></details></details><div class="op-states">${states}</div></section>`;
}

function signingOperationJson(operation) {
  if (!Array.isArray(operation)) return {};
  const body = operation[1] || {};
  let parsed = body;
  try { parsed = { ...body, json: JSON.parse(body.json) }; } catch (_) {}
  return { type: operation[0], body: parsed };
}

export function signingFocusHtml(review) {
  const chunks = review?.payload?.chunks || [];
  const states = review.signingStates || chunks.map(() => 'pending');
  const currentIndex = Math.min(review.signingIndex || 0, Math.max(chunks.length - 1, 0));
  const current = chunks[currentIndex];
  const raw = current?.operations || [];
  const operations = raw.map(signingOperationJson);
  // one readable line per
  // operation; the exact JSON stays one click away, folded.
  const operationRows = operations.map((operation, index) => {
    const { meaning, auth } = operationSummary(raw[index]);
    return `<li><details class="ch-signing-op"><summary><span class="ch-signing-op-n">${index + 1}</span><span class="ch-signing-op-text"><strong>${esc(meaning)}</strong><small>${esc(operation.type)} · ${esc(auth)}</small></span></summary><pre>${esc(JSON.stringify(operation.body, null, 2))}</pre></details></li>`;
  }).join('');
  const progress = chunks.map((chunk, index) => {
    const state = states[index] || 'pending';
    const label = state === 'current' ? 'Signing now' : state === 'signed' ? 'Signed · awaiting confirmation' : state === 'confirmed' ? 'Confirmed' : state === 'attention' ? 'Needs attention' : 'Waiting';
    return `<li class="ch-signing-progress is-${state}"><span class="ch-signing-dot" aria-hidden="true"></span><span><strong>Transaction ${index + 1} of ${chunks.length}</strong><small>${esc(label)} · ${(chunk.operations || []).length} operation${(chunk.operations || []).length === 1 ? '' : 's'}</small></span></li>`;
  }).join('');
  return `<section class="ch-signing-focus" aria-label="Changes signing run"><header><div><h2>Signing changes</h2><p>Only this signing run is shown until every request is handled.</p></div><span class="ch-signing-live" role="status" aria-live="polite">${states[currentIndex] === 'current' ? 'Waiting for Keychain' : 'Preparing next request'}</span></header><section class="ch-signing-operation" aria-label="Exact operation in the current wallet request"><h3>Signing now</h3><p>${operations.length} operation${operations.length === 1 ? '' : 's'} in the request in front of Keychain. Open one to see its exact data.</p><ol>${operationRows || '<li>No operation preview is available; signing is blocked.</li>'}</ol></section><ol class="ch-signing-progress" aria-label="Signing progress">${progress}</ol></section>`;
}

function operationResultName(operation, entries = []) {
  const body = operation?.[1] || {};
  let parsed = {};
  try { parsed = typeof body.json === 'string' ? JSON.parse(body.json) : (body.json || {}); } catch (error) { parsed = {}; }
  const worksite = String(parsed.op || '').match(/^worksite_(.+)_construction$/);
  if (worksite) return `${worksite[1]} construction`;
  const kinds = { 'STK-LND-PCR': 'Power Core', 'STK-LND-WKR': 'Worker', 'STK-LND-TOT': 'Totem', 'STK-LND-TTL': 'Title', 'STK-LND-RUNI': 'Runi' };
  const labels = [];
  for (const direction of ['unstake', 'stake']) {
    for (const asset of [...(parsed[direction]?.cards || []), ...(parsed[direction]?.items || [])]) {
      const uid = asset.card_uid || asset.item_uid;
      const matches = (saved, ref) => [ref, saved?.uid, saved?.card_uid, saved?.item_uid].includes(uid);
      const entry = uid && entries.find((entry) => entry.deed_uid === parsed.deed_uid && (
        direction === 'stake' ? entry.action !== 'unstake' && matches(entry._asset, entry.asset_ref)
          : entry.action === 'replace' ? matches(entry._previous_asset, entry.evict_asset_ref)
            : entry.action === 'unstake' && matches(entry._previous_asset || entry._asset, entry.asset_ref)
      ));
      const saved = direction === 'unstake' ? entry?._previous_asset || entry?._asset : entry?._asset;
      const name = saved?.name || saved?.card_name || (direction === 'stake' || entry?.action === 'unstake' ? entry?._asset_name : null) || uid || 'asset';
      const worker = asset.stake_type_uid === 'STK-LND-WKR' || entry?.group_id === 'worker';
      const index = entry?.slot_index;
      const slot = direction === 'stake' && asset.slot != null ? Number(asset.slot)
        : index != null && Number.isInteger(Number(index)) ? Number(index) + 1 : null;
      const location = worker && Number.isInteger(slot) && slot > 0 ? `Worker slot ${slot}`
        : kinds[asset.stake_type_uid] || (worker ? 'Worker' : asset.card_uid ? 'Card' : 'Asset');
      labels.push(`${direction === 'stake' ? 'Stake' : 'Unstake'} ${name} · ${location}`);
    }
  }
  return labels.join('; ') || body.id || 'operation';
}

function perOperationResults(result, chunk) {
  const rows = Array.isArray(result?.op_results) ? result.op_results : result?.game?.evidence?.operations;
  if (!Array.isArray(rows) || !rows.length) return [];
  const byIndex = new Map(rows.map((row) => [Number(row?.index), row]));
  return (chunk?.operations || []).map((operation, index) => {
    const row = byIndex.get(index);
    const outcome = row?.success === true ? 'success'
      : row?.success === false ? (row.error || 'refused') : 'game result not available yet';
    return { index, label: operationResultName(operation, chunk?.receiptEntries || []), success: row?.success, outcome };
  });
}

function operationResultsHtml(results) {
  if (!Array.isArray(results) || !results.length) return '';
  return `<div class="ceres-pill-op-results" role="list">${results.map((result) => `<div role="listitem" class="${result.success === true ? 'is-confirmed' : result.success === false ? 'is-failed' : 'is-unresolved'}"><span>Operation ${result.index + 1} · ${esc(result.label)}</span><span> — ${esc(result.outcome)}</span></div>`).join('')}</div>`;
}

function setVerificationNotice(stateEl, notice) {
  setVerificationNotices(stateEl, notice ? [notice] : []);
}

function setVerificationNotices(stateEl, notices, opResults = []) {
  if (!stateEl) return;
  const visible = Array.isArray(notices) ? notices : [];
  stateEl.innerHTML = renderNotices(visible, 'op-state-notice') + operationResultsHtml(opResults);
  stateEl.classList.toggle('is-success', visible.some((notice) => notice?.code === 'verification_confirmed'));
  stateEl.classList.toggle('is-error', visible.some((notice) => notice?.severity === 'danger'));
}

// defect 3 -- every code here is a non-terminal verdict (never
// domain-confirmed/domain-mismatch/domain-not-applied), so the fallback notice this function builds
// only used when the server's own `notices[]` is unexpectedly empty -- still offers a
// real re-check action rather than leaving the player with nothing to do.
const RETRYABLE_VERIFICATION_CODES = new Set([
  'verification_pending', 'verification_pending_escalated', 'verification_could_not_verify',
  'verification_chain_check', 'verification_lookup_failed', 'verification_not_found',
  'verification_domain_check_failed',
]);

function verificationNoticeForResult(result, fallbackState = 'chain_absent') {
  const serverNotice = Array.isArray(result?.notices)
    ? result.notices.find((notice) => notice.kind === 'verification')
    : null;
  if (serverNotice) return serverNotice;
  const state = result?.state || fallbackState;
  const code = state === 'domain-confirmed' ? 'verification_confirmed'
    : state === 'game-refused' ? 'verification_game_refused'
    : state === 'domain-mismatch' ? 'verification_mismatch'
      : state === 'domain-not-applied' ? 'verification_not_applied'
      : state === 'domain-pending' ? 'verification_pending'
        : state === 'domain-could-not-verify' ? 'verification_could_not_verify'
          : state === 'chain_verified' ? 'verification_chain_check'
            : result?.chain?.evidence?.reason_kind === 'lookup_failed' ? 'verification_lookup_failed'
              : 'verification_not_found';
  const action = RETRYABLE_VERIFICATION_CODES.has(code) ? { id: 'retry_verification', label: 'Check again' } : undefined;
  const reason = result?.evidence?.reason || result?.game?.evidence?.reason || result?.game?.evidence?.error || result?.chain?.evidence?.reason || result?.domain?.evidence?.reason;
  return { code, kind: NOTICE_CATALOG[code]?.kind, severity: NOTICE_CATALOG[code]?.severity, blocking: NOTICE_CATALOG[code]?.blocking, reason: state === 'game-refused' ? reason || 'The game supplied no refusal reason.' : reason, evidence: result?.evidence || result?.game?.evidence || result?.chain?.evidence || result?.domain?.evidence || {}, action };
}

// what the user would sign -- the operations and hazards, nothing display-only.
// Each op's `n` is a fresh random nonce per build (fides_land/ops.py::_nonce), so it is
// dropped; everything else in the op must match for a refreshed preview to count as the same.
function previewSignature(payload) {
  const stable = (operation) => {
    const [name, body] = Array.isArray(operation) ? operation : [];
    let json = body?.json;
    try { const parsed = JSON.parse(json); delete parsed.n; json = parsed; } catch (_) { /* keep raw */ }
    return [name, { ...(body || {}), json }];
  };
  return JSON.stringify([(payload?.chunks || []).map((chunk) => (chunk.operations || []).map(stable)), payload?.hazards || []]);
}

const CAPTURE_REFRESH_ATTEMPTS = 3;

export function previewIncludedEntries(stagedEntries, excludedMoves) {
  const excluded = new Set((excludedMoves || []).map((move) => `${move?.deed_uid}|${move?.action}|${move?.asset_ref}`));
  const excludedIndexes = new Set((excludedMoves || [])
    .map((move) => move?.index).filter((index) => Number.isInteger(index) && index >= 0));
  return (stagedEntries || []).filter((entry, index) => (
    !excludedIndexes.has(index) && !excluded.has(`${entry?.deed_uid}|${entry?.action}|${entry?.asset_ref}`)
  ));
}

// whether the surface a preview belongs to is still on screen (Changes window open, or the
// plot row still mounted) -- a silent re-ask stops once nobody is looking at it.
function previewStillShown(portfolio, row) {
  if (portfolio) return !!S.CERES_CHECKOUT_OPEN;
  return !!document.querySelector(`[data-deed-uid="${CSS.escape(String(row?.deed_uid || ''))}"]`);
}

export async function requestOperationPreview(target) {
  const portfolio = target?.scope === 'portfolio';
  const key = previewKey(target);
  const row = portfolio ? null : target;
  const priorPayload = CERES_PREVIEWS.get(key)?.payload || null;
  const state = { loading: true, error: null, payload: null };
  CERES_PREVIEWS.set(key, state);
  if (portfolio) renderCheckoutSurface();
  else updateMountedRow(row.deed_uid);
  // Item 25 -- snapshot the EXACT CERES_STAGE entries this preview covers, at
  // the moment the preview is fetched (before any signing happens). This is
  // what later lets a confirmed broadcast clear precisely the moves that
  // were actually included in it, never anything staged afterward that
  // happens to share a deed_uid -- see verifyBroadcast()'s own comment.
  const stagedEntries = portfolio
    ? checkoutSignableEntries()
    : stagedEntriesForRow(row.deed_uid).filter((entry) => !entry._plan_invalid_reason); // set-aside moves are not sent
  state.stagedSnapshot = stagedEntries;
  state.requestedSnapshot = stagedEntries;
  try {
    const endpoint = portfolio ? '/api/op-preview' : `/api/plots/${encodeURIComponent(row.deed_uid)}/op-preview`;
    const moves = stagedEntries.map(stageMovePayload);
    const stagedWorksites = [...CERES_STAGED_WORKSITES.entries()]
      .filter(([deedUid]) => portfolio || deedUid === row.deed_uid)
      .map(([deedUid, build]) => ({ deed_uid: deedUid, target: build.target }));
    state.stagedWorksitesSnapshot = stagedWorksites;
    state.requestedStageSignature = JSON.stringify({ moves, stagedWorksites });
    // The freshest combine_verified_at among the entries THIS request covers -- read at
    // request-body level (ceres/routes_op_preview.py::_preview), never per-move. A stage
    // entry only carries this when it was staged via the tile combine-then-stake flow
    // (handleCombineStakePromptAction); an ordinary staged move has no such field and
    // this stays undefined.
    const combineVerifiedAt = stagedEntries
      .map((entry) => entry.combine_verified_at).filter(Boolean).sort().pop();
    // every preview names the board read it was staged from; the server
    // refuses a stake/replace when it holds a newer capture (or no stamp at all).
    // that refusal is never shown -- the page adopts the newer capture and
    // re-prepares the preview on it (bounded), so the stake proceeds on fresh data.
    let response;
    let payload;
    let refreshed = false;
    let liveAsks = 0;
    for (let attempt = 0; ; ) {
      const requestBody = { moves, staged_worksites: stagedWorksites, capture_generated_at: BOARD?.generated_at || null };
      if (combineVerifiedAt) requestBody.combine_verified_at = combineVerifiedAt;
      response = await fetch(playerQueryAppend(endpoint), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });
      payload = await response.json().catch(() => ({}));
      state.captureStamp = requestBody.capture_generated_at;
      if (response.status === 503 && payload?.detail?.code === 'live_read_pending') {
        // the game has not answered the Sign-time live read yet -- keep "Preparing the
        // transaction…" and ask again (1 s, 2 s, 4 s, then every 8 s), never an error text. A newer
        // preview for the same scope, or a closed Changes window / unmounted row, ends the re-asking.
        state.refreshing = true;
        if (portfolio) renderCheckoutSurface();
        else updateMountedRow(row.deed_uid);
        await new Promise((resolve) => setTimeout(resolve, [1000, 2000, 4000][liveAsks] ?? 8000));
        liveAsks += 1;
        if (CERES_PREVIEWS.get(key) !== state || !previewStillShown(portfolio, row)) return;
        continue;
      }
      if (response.status !== 409 || payload?.detail?.code !== 'capture_superseded' || attempt + 1 >= CAPTURE_REFRESH_ATTEMPTS) break;
      attempt += 1;
      refreshed = true;
      state.refreshing = true;
      if (portfolio) renderCheckoutSurface();
      else updateMountedRow(row.deed_uid);
      await adoptLatestCapture();
    }
    state.refreshing = false;
    if (response.status === 409 && payload?.detail?.code === 'plan_changed') {
      // the live read shows these staged cards are no longer where the plan put them. They take
      // the existing kept-plan "no longer valid" state () and the rest is prepared again.
      for (const index of payload.detail.moves || []) {
        const entry = stagedEntries[index];
        if (entry && !entry._plan_invalid_reason) entry._plan_invalid_reason = 'card_moved';
      }
      if ((payload.detail.moves || []).length && CERES_PREVIEWS.get(key) === state) return requestOperationPreview(target);
    }
    if (!response.ok) {
      const detail = payload.detail;
      state.errorNotices = detail?.errors?.flatMap((item) => item.notice ? [item.notice] : []) || [];
      const message = typeof detail === 'string' ? detail : detail?.errors?.map((item) => item.reason).join('; ') || detail?.message || `HTTP ${response.status}`;
      throw new Error(message);
    }
    stagedEntries.forEach((entry, index) => {
      const excluded = (payload.excluded_moves || []).find((move) => move.index === index);
      entry._worksite_parked_reason = excluded?.reason?.startsWith('Parked') ? excluded.reason : null;
    });
    if ((payload?.excluded_moves || []).length) {
      state.stagedSnapshot = previewIncludedEntries(stagedEntries, payload.excluded_moves);
    }
    for (const build of stagedWorksites) {
      const staged = CERES_STAGED_WORKSITES.get(build.deed_uid);
      if (staged) {
        staged.parkedReason = (payload.parked_worksites || []).find((item) => item.deed_uid === build.deed_uid)?.reason || null;
        if (staged.parkedReason) staged.blockedReason = null;
      }
    }
    state.loading = false;
    state.payload = payload;
    state.refreshedNotice = refreshed && !!priorPayload && previewSignature(priorPayload) !== previewSignature(payload);
  } catch (error) {
    state.loading = false;
    state.error = String(error);
  }
  if (portfolio) renderCheckoutSurface();
  else updateMountedRow(row.deed_uid);
}

// Item 25 -- parses one chunk's Hive ops back into the (deed_uid, direction)
// pairs it actually contains, so a confirmed broadcast can clear EXACTLY the
// staged moves it covers -- never the whole stage, never a different chunk's
// moves. `fides_land/ops.py`'s `intents_to_ops()` groups intents by
// `(deed_uid, direction)` and emits ONE op per group -- a deed with BOTH a
// stake and an unstake queued gets TWO separate ops, so this deliberately
// keys on deed+direction together, not deed alone (keying on deed alone
// would wrongly clear an unstake move the moment a same-deed stake op
// confirmed, even if the unstake op was still pending or failed). An op that
// fails to parse contributes nothing to the returned set -- never guessed
// per this item's own "never clear on a guess" rule.
function chunkDeedDirections(chunk) {
  const keys = new Set();
  for (const op of chunk?.operations || []) {
    try {
      const body = op[1];
      const parsed = typeof body?.json === 'string' ? JSON.parse(body.json) : body?.json; // a stored op may already be parsed
      const deedUid = parsed?.deed_uid;
      if (!deedUid) continue;
      const hasStake = parsed.stake && (((parsed.stake.cards || []).length) || ((parsed.stake.items || []).length));
      const hasUnstake = parsed.unstake && (((parsed.unstake.cards || []).length) || ((parsed.unstake.items || []).length));
      if (hasStake) keys.add(`${deedUid}|stake`);
      if (hasUnstake) keys.add(`${deedUid}|unstake`);
    } catch (error) { /* unparsable op -- contributes nothing, never a guess */ }
  }
  return keys;
}

function chunkWorksiteBuilds(chunk) {
  const builds = new Map();
  for (const op of chunk?.operations || []) {
    try {
      const body = op[1];
      const parsed = typeof body?.json === 'string' ? JSON.parse(body.json) : body?.json;
      const operation = String(parsed?.op || '');
      if (!operation.startsWith('worksite_') || !operation.endsWith('_construction')) continue;
      const target = operation.slice('worksite_'.length, -'_construction'.length);
      (parsed.deed_uids || []).forEach((deedUid) => builds.set(String(deedUid), target));
    } catch (error) { /* unparsable op contributes nothing */ }
  }
  return builds;
}

function stageEntryMatchesChunk(entry, keys, chunk = null) {
  if (Array.isArray(chunk?.stage_groups) && !chunk.stage_groups.includes(entry.group_id)) return false;
  return keys.has(`${entry.deed_uid}|${entry.action}`)
    || (entry.action === 'replace' && keys.has(`${entry.deed_uid}|stake`));
}

// Item 25 -- clears ONLY the CERES_STAGE entries this specific confirmed
// chunk actually covers, matched against `review.stagedSnapshot` (captured
// at PREVIEW time, in requestOperationPreview() -- before any signing
// happened, so a move staged AFTER "Prepare transaction" was clicked can
// never be wrongly cleared just because it shares a deed_uid). Matching by
// object REFERENCE via indexOf(): if the same slot was re-staged after the
// snapshot was taken, stageForSlot() replaces that array slot with a NEW
// entry object -- the old (now-broadcast) object is already gone from
// CERES_STAGE, indexOf correctly finds nothing, and the newer, un-broadcast
// entry is left untouched. This is also the single place that keeps the
// Review button and the checkout panel in sync (the owner: "connected to the
// checkout action") -- renderCheckoutSurface() always runs here, regardless
// of whether the confirming broadcast was started from a row's own Review
// button or from the portfolio checkout panel.
function clearConfirmedChunkEntries(review, chunk) {
  const keys = chunkDeedDirections(chunk);
  const worksiteBuilds = chunkWorksiteBuilds(chunk);
  const snapshot = review?.stagedSnapshot || [];
  let toRemove = keys.size ? snapshot.filter((entry) => stageEntryMatchesChunk(entry, keys, chunk)) : [];
  // after a reload the snapshot is the restored record's entries. Every signed entry carries its transaction
  // id -- the stable key -- so when the snapshot no longer lines up with the chunk (an op that would not parse, a
  // snapshot from an earlier load), the entries signed under this transaction are cleared instead of left drawn
  // over the confirmed plots. Only a transaction that matches nothing at all is reported as unmatched -- one row.
  const txId = chunk?.txId || snapshot.find((entry) => entry?.txId)?.txId || null;
  if (!toRemove.length && txId) toRemove = CERES_STAGE.filter((entry) => entry?.txId === txId);
  const worksiteSnapshots = review?.stagedWorksitesSnapshot || [];
  const confirmedWorksites = worksiteSnapshots.filter((build) => worksiteBuilds.get(String(build.deed_uid)) === build.target);
  if (!toRemove.length && !confirmedWorksites.length) {
    setConfirmationBannerState(
      noticeMessage(catalogNotice('verification_confirmed_stage_unmatched')),
      '',
      'caution',
      { state: 'is-confirmed-stale', phase: 'confirmed-partial', aria: 'polite', txId },
    );
    return [];
  }
  if (txId) forgetInFlightRecord(txId, { removeStage: false });
  const affectedDeeds = new Set();
  toRemove.forEach((entry) => {
    recordChainUnstake(entry, 'confirmed', txId);
    let index = CERES_STAGE.indexOf(entry);
    // a restored entry re-created by a later load is the same move under the same transaction.
    if (index === -1 && entry?.txId) index = CERES_STAGE.findIndex((item) => item?.txId === entry.txId && opKey(item) === opKey(entry));
    if (index !== -1) CERES_STAGE.splice(index, 1);
    affectedDeeds.add(entry.deed_uid);
    if (entry._restake) { const parent = applyConfirmedRestake(entry); if (parent) affectedDeeds.add(parent.deed_uid); } // phase 4 (K4)
  });
  confirmedWorksites.forEach((build) => {
    const current = CERES_STAGED_WORKSITES.get(build.deed_uid);
    if (current?.target === build.target && current?.txId === txId) CERES_STAGED_WORKSITES.delete(build.deed_uid);
    affectedDeeds.add(build.deed_uid);
  });
  CERES_PREVIEWS.delete(CERES_PORTFOLIO_KEY);
  affectedDeeds.forEach((deedUid) => {
    CERES_PREVIEWS.delete(deedUid);
    updateMountedRow(deedUid);
  });
  if (toRemove.some((entry) => entry.chain_id)) window.dispatchEvent(new CustomEvent('ceres:chain-unstake-confirmed', { detail: { ids: [...new Set(toRemove.map((entry) => entry.chain_id).filter(Boolean))] } }));
  if (toRemove.length) setConfirmationBanner(toRemove);
  renderCheckoutSurface();
  return toRemove;
}

function confirmationMessage(entries) {
  const runiRemoved = entries.some((entry) => entry.group_id === 'runi' && entry.action === 'unstake');
  if (runiRemoved) return 'Runi removed from the Power slot. This plot gave up the +100% production boost, 1,500 base PP above the 100,000 cap, and the zero-DEC staking requirement. Runi has no unstake cooldown and can be restaked immediately.';
  return `${entries.length} move${entries.length === 1 ? '' : 's'} applied.`;
}

function confirmationRole() {
  return CERES_CONFIRMATION_BANNER?.aria === 'assertive' ? 'alert' : 'status';
}

function bindConfirmationBanner(el) {
  el.addEventListener('click', (event) => {
    const action = event.target.closest('[data-confirmation-action]');
    if (!action) return;
    if (action.dataset.confirmationAction === 'dismiss') dismissConfirmationPill('dismiss');
    if (action.dataset.confirmationAction === 'retry-verification' || action.dataset.confirmationAction === 'refresh') CERES_CONFIRMATION_BANNER?.retry?.();
    // Receipts removed; see library/ceres-receipts-removal.md
  });
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && el.contains(document.activeElement)) dismissConfirmationPill('escape');
  });
  el.addEventListener('animationend', (event) => {
    if (event.animationName === 'ceres-pill-countdown' && CERES_CONFIRMATION_BANNER?.state === 'is-confirmed') {
      dismissConfirmationPill('timer');
    }
  });
}

function ensureConfirmationBanner() {
  let el = document.getElementById('ceres-confirmation-banner');
  const role = confirmationRole();
  if (el && el.getAttribute('role') !== role) {
    el.remove();
    el = null;
  }
  if (el) {
    el.setAttribute('aria-live', role === 'alert' ? 'assertive' : 'polite');
    return el;
  }
  el = document.createElement('div');
  el.id = 'ceres-confirmation-banner';
  el.className = 'ceres-pill worksite-banner';
  el.hidden = true;
  el.setAttribute('role', role);
  el.setAttribute('aria-live', role === 'alert' ? 'assertive' : 'polite');
  document.body.appendChild(el);
  bindConfirmationBanner(el);
  document.addEventListener('visibilitychange', () => {
    el.classList.toggle('is-paused', document.hidden);
  });
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.addEventListener?.('change', () => renderConfirmationBanner());
  return el;
}

function reducedMotionPreferred() {
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

function confirmationPhase(title, state) {
  const copy = String(title || '').toLowerCase();
  if (state === 'is-confirmed-partial' || state === 'is-confirmed-stale') return 'confirmed-partial';
  if (state === 'is-confirmed') return 'confirmed';
  if (copy.includes('refused')) return 'refused';
  if (copy.includes('mismatch') || copy.includes('does not match')) return 'mismatch';
  if (copy.includes('could not') || copy.includes('cannot verify') || copy.includes('not found on chain')) return 'could-not-verify';
  if (copy.includes('still not confirmed')) return 'pending-escalated';
  if (copy.includes('not confirmed') || copy.includes('awaiting confirmation')) return 'pending';
  if (copy.includes('confirmed') || copy.includes('confirmation complete')) return 'confirmed';
  if (state === 'is-failed') return 'refused';
  if (state === 'is-sent' || state === 'is-queued') return 'accepted';
  if (state === 'is-unresolved' || state === 'is-pending') return 'pending';
  return state || 'pending';
}

function confirmationPhaseRank(phase) {
  if (phase === 'accepted') return 0;
  if (phase === 'pending') return 1;
  if (phase === 'pending-escalated' || phase === 'could-not-verify') return 2;
  if (['confirmed', 'confirmed-partial', 'refused', 'mismatch'].includes(phase)) return 3;
  return 1;
}

function entriesForChunk(review, chunk) {
  const keys = chunkDeedDirections(chunk);
  return (review?.stagedSnapshot || []).filter((entry) => stageEntryMatchesChunk(entry, keys, chunk));
}

function entryFacts(entries) {
  const deedEntries = entries.filter((entry) => entry?.deed_uid);
  const plots = [...new Set(deedEntries.map((entry) => entry.plot ?? BOARD?.plots?.find((plot) => plot.deed_uid === entry.deed_uid)?.plot)
    .filter((plot) => plot !== undefined && plot !== null && plot !== ''))];
  const when = entries.map((entry) => entry?.broadcast_at).filter(Boolean).sort().pop();
  const humanWhen = when ? humanizeInstant(when) : null;
  return {
    where: plots.length === 1 ? formatPlotLabel(plots[0]) : plots.length > 1 ? `${plots.length} plots` : null,
    what: entries.length ? `${entries.length} move${entries.length === 1 ? '' : 's'} applied` : null,
    when: humanWhen?.relative || null,
    receiptAt: when || null,
  };
}

function truncatedTxId(txId) {
  const value = String(txId || '');
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function confirmationHeadline(notice) {
  if (notice?.code === 'verification_confirmed') return 'Transaction confirmed';
  if (notice?.code === 'verification_waiting' || notice?.code === 'verification_in_flight') return 'Transaction sent';
  if (notice?.code === 'verification_pending') return 'Awaiting confirmation';
  if (notice?.code === 'verification_mismatch') return 'Game state does not match';
  if (notice?.code === 'verification_not_applied') return 'The game did not apply this change';
  if (notice?.code === 'verification_game_refused') return 'The game refused this operation';
  if (notice?.code === 'verification_pending_escalated') return 'Still not confirmed';
  if (notice?.code === 'verification_inclusion_unobserved') return 'Broadcasted, inclusion not observed';
  return noticeMessage(notice);
}

function toneForNotice(notice) {
  return notice?.severity === 'danger' ? 'danger'
    : notice?.severity === 'success' ? 'success'
      : notice?.severity === 'info' ? 'info' : 'caution';
}

function stateForNotice(notice) {
  if (notice?.code === 'verification_confirmed') return 'is-confirmed';
  if (notice?.code === 'verification_mismatch' || notice?.code === 'verification_not_applied' || notice?.code === 'verification_game_refused') return 'is-failed';
  if (notice?.code === 'verification_waiting' || notice?.code === 'verification_in_flight') return 'is-sent';
  return 'is-unresolved';
}

function confirmationFactHtml(label, value, key) {
  return `<div class="ceres-pill-fact" data-fact="${key}"><span class="ceres-pill-label">${label}</span><span class="ceres-pill-value">${esc(value || '—')}</span></div>`;
}

function shortReceiptLine(value, limit) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
}

function renderConfirmationBanner() {
  const el = ensureConfirmationBanner();
  if (!CERES_CONFIRMATION_BANNER) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const banner = CERES_CONFIRMATION_BANNER;
  const phase = banner.phase || confirmationPhase(banner.title, banner.state);
  // with several transactions, the line whose transaction the banner is about takes the banner's state
  // the others keep what they last showed. The pill auto-closes only once every line is confirmed.
  let batchHtml = '';
  let batchDone = true;
  if (CERES_BATCH) {
    CERES_BATCH.lines.forEach((line) => {
      if (line.chunk.txId && line.chunk.txId === banner.txId) {
        line.state = banner.state;
        line.text = banner.title;
        line.txId = banner.txId;
        if (banner.opResults?.length) line.opResults = banner.opResults;
      }
      if (line.state !== 'is-confirmed') batchDone = false;
    });
    const batchResults = CERES_BATCH.lines.map((line) => line.opResults?.length
      ? `<section class="ceres-pill-transaction-results"><strong>Transaction ${line.index + 1} operation results</strong>${operationResultsHtml(line.opResults)}</section>` : '').join('');
    batchHtml = `<ol class="ceres-pill-batch">${CERES_BATCH.lines.map((line) => `<li class="${esc(line.state)}"><span>Transaction ${line.index + 1} of ${CERES_BATCH.lines.length}</span><span>${esc(line.text)}</span>${line.txId ? `<a href="${esc(HIVE_TX_EXPLORER(line.txId))}" target="_blank" rel="noopener noreferrer">${esc(truncatedTxId(line.txId))}</a>` : ''}</li>`).join('')}</ol>${batchResults}`;
  }
  const facts = [
    confirmationFactHtml('WHERE', banner.where, 'where'),
    confirmationFactHtml('WHAT', banner.what, 'what'),
    confirmationFactHtml('WHEN', banner.when || (banner.receiptAt ? new Date(banner.receiptAt).toLocaleTimeString() : null), 'when'),
  ].join('');
  const transaction = banner.txId
    ? `<div class="ceres-pill-transaction"><a href="${esc(HIVE_TX_EXPLORER(banner.txId))}" target="_blank" rel="noopener noreferrer" title="Opens the transaction on a Hive block explorer">View on Hive <span aria-hidden="true">↗</span><span class="ceres-pill-txid">${esc(truncatedTxId(banner.txId))}</span></a></div>`
    : '<div class="ceres-pill-transaction is-empty" aria-hidden="true"><span>View on Hive</span></div>';
  const actionName = banner.action?.id === 'refresh_confirmation' ? 'refresh' : 'retry-verification';
  const emptyAction = '<span class="ceres-pill-action-slot is-empty" aria-hidden="true"></span>';
  const actionButton = banner.action?.label
    ? `<button type="button" class="ceres-pill-action ceres-btn ceres-btn--secondary" data-control-density="compact" data-confirmation-action="${actionName}">${esc(banner.action.label)}</button>` : '';
  // "Open receipt" used to keep this row filled; an invisible twin of the action button now reserves its height so
  // the pill does not jump when "Check again" appears or goes ( stable pill).
  const placeholder = '<button type="button" class="ceres-pill-action ceres-btn ceres-btn--secondary" data-control-density="compact" tabindex="-1" aria-hidden="true" style="visibility:hidden">Check again</button>';
  const action = `${emptyAction}<span class="ceres-pill-action-slot is-main">${actionButton || placeholder}</span>`;
  const batchIncomplete = CERES_BATCH && !batchDone;
  const batchStopped = batchIncomplete && CERES_BATCH.lines.some((line) => ['is-failed', 'is-aborted'].includes(line.state));
  const title = batchStopped ? 'Batch partially completed' : batchIncomplete ? 'Batch in progress' : banner.title;
  const detail = phase === 'refused' ? banner.detail
    : batchIncomplete ? `${CERES_BATCH.lines.filter((line) => line.state === 'is-confirmed').length} of ${CERES_BATCH.lines.length} confirmed`
      : banner.detail?.trim() !== banner.title?.trim() ? banner.detail : '';
  const shortTitle = shortReceiptLine(title, 48) || 'Transaction update';
  const shortDetail = shortReceiptLine(detail, 72) || 'Transaction status updated.';
  const spinner = banner.state === 'is-sent' || banner.waiting ? '<span class="spinner" aria-hidden="true"></span>' : '';
    const progress = banner.state === 'is-confirmed' && batchDone && !reducedMotionPreferred() && banner.txId
    ? '<div class="ceres-pill-progress" aria-hidden="true"></div>' : '';
  el.hidden = false;
  const complete = banner.state === 'is-confirmed' && batchDone;
  const mark = spinner || (complete ? '<span class="ceres-pill-success-icon" aria-hidden="true">✓</span>' : '');
  el.className = `ceres-pill worksite-banner tone-${banner.tone} ${banner.state} phase-${phase}${complete ? ' is-complete' : ''}${document.hidden ? ' is-paused' : ''}`;
  el.innerHTML = `<div class="ceres-pill-title-row"><strong class="ceres-pill-title worksite-banner-title"><span class="ceres-pill-phase-mark">${mark}</span><span>${esc(shortTitle)}</span></strong><button type="button" class="ceres-pill-dismiss worksite-banner-dismiss ceres-btn ceres-btn--secondary" data-control-density="compact" data-confirmation-action="dismiss" aria-label="Dismiss">✕</button></div><div class="ceres-pill-content"><div class="ceres-pill-detail worksite-banner-detail" title="${esc(detail || 'Transaction status updated.')}">${esc(shortDetail)}</div><div class="ceres-pill-facts">${facts}</div>${transaction}<div class="ceres-pill-action-row">${action}</div></div>${progress}`;
}

function setConfirmationBanner(entries) {
  const runiRemoved = entries.some((entry) => entry.group_id === 'runi' && entry.action === 'unstake');
  CERES_CONFIRMATION_BANNER = {
    title: runiRemoved ? 'Runi unstaked' : 'Transaction confirmed',
    detail: confirmationMessage(entries),
    tone: runiRemoved ? 'caution' : 'success',
    state: 'is-confirmed',
    aria: 'polite',
    txId: entries.find((entry) => entry?.txId)?.txId || null,
    ...entryFacts(entries),
  };
  renderConfirmationBanner();
}

function dismissConfirmationPill(reason) {
  if (!CERES_CONFIRMATION_BANNER) return;
  CERES_CONFIRMATION_BANNER = null;
  CERES_BATCH = null;
  renderConfirmationBanner();
}

// exported -- the worksite modal reports through the same pill once it has closed on broadcast.
export function setConfirmationBannerState(title, detail, tone = 'success', options = {}) {
  const priorTxId = CERES_CONFIRMATION_BANNER?.txId || null;
  const nextTxId = options.txId !== undefined ? options.txId : priorTxId;
  const nextState = options.state || (tone === 'danger' ? 'is-failed' : tone === 'success' ? 'is-confirmed' : tone === 'info' ? 'is-sent' : 'is-unresolved');
  const phase = options.phase || confirmationPhase(title, nextState);
  const priorPhase = nextTxId && nextTxId === priorTxId ? CERES_CONFIRMATION_BANNER?.phase : null;
  if (priorPhase && (priorPhase === phase || confirmationPhaseRank(priorPhase) > confirmationPhaseRank(phase))) {
    let changed = false;
    if (priorPhase === phase && options.action?.label && !CERES_CONFIRMATION_BANNER.action) {
      CERES_CONFIRMATION_BANNER.action = options.action;
      CERES_CONFIRMATION_BANNER.retry = options.retry || null;
      changed = true;
    }
    // the same phase can stop or resume automatic checks; the spinner and its copy follow.
    if (priorPhase === phase && CERES_CONFIRMATION_BANNER.waiting !== (options.waiting === true)) {
      Object.assign(CERES_CONFIRMATION_BANNER, { waiting: options.waiting === true, title, detail });
      changed = true;
    }
    if (changed) renderConfirmationBanner();
    return;
  }
  CERES_CONFIRMATION_BANNER = {
    title,
    detail,
    tone,
    waiting: options.waiting === true,
    state: nextState,
    phase,
    aria: options.aria || 'polite',
    txId: nextTxId,
    where: options.where !== undefined ? options.where : CERES_CONFIRMATION_BANNER?.where || null,
    what: options.what !== undefined ? options.what : CERES_CONFIRMATION_BANNER?.what || null,
    when: options.when !== undefined ? options.when : CERES_CONFIRMATION_BANNER?.when || null,
    opResults: options.opResults !== undefined ? options.opResults : nextTxId === priorTxId ? CERES_CONFIRMATION_BANNER?.opResults || [] : [],
    action: options.action || null,
    retry: options.retry || null,
    receiptAt: options.receiptAt || (nextTxId === priorTxId ? CERES_CONFIRMATION_BANNER?.receiptAt || new Date().toISOString() : new Date().toISOString()),
  };
  renderConfirmationBanner();
}

// defect 2 -- {chunk, review, txId} for the checkout's `.op-state` element
// currently mid-verification, keyed by the element itself (a WeakMap so a discarded
// review row's entry is never a leak). Set on every verifyBroadcast() call and read by
// the "Check again" notice action's click handler so a manual re-check re-uses the same
// chunk/txId rather than needing them threaded through the DOM.
export const CHECKOUT_VERIFY_TARGETS = new WeakMap();

function markBroadcastedChunk(review, chunk, txId, queued = false) {
  if (typeof chunkDeedDirections !== 'function') return;
  chunk.runId = chunk.runId || review?.runId || null;
  chunk.runKind = chunk.runKind || review?.runKind || null;
  chunk.entryId = chunk.entryId || operationEntryId();
  chunk.txId = chunk.txId || txId; // the pill's batch lines find their transaction by it
  const keys = chunkDeedDirections(chunk);
  const entries = (review?.stagedSnapshot || []).filter((entry) => stageEntryMatchesChunk(entry, keys, chunk));
  const builds = chunkWorksiteBuilds(chunk);
  const worksites = (review?.stagedWorksitesSnapshot || [])
    .filter((build) => builds.get(String(build.deed_uid)) === build.target)
    .map((build) => ({ ...CERES_STAGED_WORKSITES.get(build.deed_uid), ...build }));
  const broadcastAt = entries[0]?.broadcast_at || worksites[0]?.broadcast_at || new Date().toISOString();
  entries.forEach((entry) => {
    entry.txId = entry.txId || txId;
    if (queued) entry.queued_at = entry.queued_at || broadcastAt;
    else entry.broadcast_at = entry.broadcast_at || broadcastAt;
    recordChainUnstake(entry, 'signed', txId, broadcastAt);
    const parent = restakeParent(entry._restake);
    if (parent) parent._unstake_tx = txId; // never send the same unstake twice
  });
  worksites.forEach((build) => {
    const current = CERES_STAGED_WORKSITES.get(build.deed_uid);
    if (current?.target === build.target) {
      current.txId = current.txId || txId;
      if (queued) current.queued_at = current.queued_at || broadcastAt;
      else current.broadcast_at = current.broadcast_at || broadcastAt;
    }
  });
  refreshChangeBadges(); // a signed change leaves the plot's "X Changes" badge
  writeInFlightRecord({
    txId,
    entryId: chunk.entryId,
    account: review?.payload?.account || CURRENT_PLAYER,
    broadcast_at: queued ? null : broadcastAt,
    queued_at: queued ? broadcastAt : null,
    scope: review?.scope || 'portfolio',
    runId: chunk.runId,
    runKind: chunk.runKind,
    operations: chunk?.operations || [],
    entries: entries.map(stripForStore),
    worksites: worksites.map((build) => ({ ...build, txId, broadcast_at: queued ? null : build.broadcast_at || broadcastAt, queued_at: queued ? broadcastAt : null })),
  });
  if (!entries.length || typeof document === 'undefined') return;
  const detail = queued ? 'Signed — queued for broadcast.' : noticeMessage(catalogNotice('verification_in_flight'));
  entries.forEach((entry) => {
    const move = [...document.querySelectorAll('#checkout-panel .checkout-move')].find((candidate) => (
      candidate.dataset.deedUid === String(entry.deed_uid)
      && candidate.dataset.groupId === String(entry.group_id)
      && candidate.dataset.slotIndex === String(entry.slot_index)
    ));
    if (!move) return;
    move.dataset.inFlight = '1';
    let status = move.querySelector('.checkout-move-status');
    if (!status) {
      status = document.createElement('span');
      status.className = 'checkout-move-status';
      move.querySelector('.checkout-move-copy')?.append(status);
    }
    status.textContent = detail;
    move.querySelector('.checkout-remove')?.setAttribute('disabled', '');
  });
  document.querySelectorAll('#checkout-panel .checkout-prepare').forEach((button) => {
    button.disabled = true;
    button.setAttribute('data-tip', detail);
    button.setAttribute('data-tip-peer', 'checkout_blockers');
    button.setAttribute('data-tip-depth', 'extended');
    button.setAttribute('aria-label', detail);
  });
  const facts = entryFacts(entries);
  if (CERES_CONFIRMATION_BANNER?.txId === txId) return;
  setConfirmationBannerState(
    queued ? 'Transaction signed — queued' : 'Transaction sent',
    detail,
    'info',
    { state: queued ? 'is-queued' : 'is-sent', aria: 'polite', txId, ...facts },
  );
}

function clearFailedChunkMarkers(review, chunk) {
  const keys = chunkDeedDirections(chunk);
  const affectedDeeds = new Set();
  const txIds = new Set();
  if (chunk?.txId) txIds.add(chunk.txId);
  (review?.stagedSnapshot || []).forEach((entry) => {
    if (entry._restake && stageEntryMatchesChunk(entry, keys, chunk)) { const parent = restakeParent(entry._restake); if (parent) delete parent._unstake_tx; }
    if (!stageEntryMatchesChunk(entry, keys, chunk) || CERES_STAGE.indexOf(entry) === -1) return;
    if (entry?.txId) txIds.add(entry.txId);
    entry.txId = null;
    entry.broadcast_at = null;
    affectedDeeds.add(entry.deed_uid);
  });
  const builds = chunkWorksiteBuilds(chunk);
  (review?.stagedWorksitesSnapshot || []).forEach((build) => {
    if (builds.get(String(build.deed_uid)) !== build.target) return;
    const current = CERES_STAGED_WORKSITES.get(build.deed_uid);
    if (current?.target !== build.target) return;
    delete current.txId;
    delete current.broadcast_at;
    affectedDeeds.add(build.deed_uid);
  });
  txIds.forEach(forgetInFlightRecord);
  affectedDeeds.forEach((deedUid) => updateMountedRow(deedUid));
  for (const [key, cachedReview] of CERES_PREVIEWS) {
    if (cachedReview === review) CERES_PREVIEWS.delete(key);
  }
  renderCheckoutSurface();
}

function gameRefusalReason(result) {
  const notice = result?.notices?.find((item) => item?.code === 'verification_game_refused');
  return notice?.reason || result?.game?.evidence?.reason || result?.game?.evidence?.error || 'The game supplied no refusal reason.';
}

function firstClearOutcomeChunks(result, chunk) {
  const rows = Array.isArray(result?.op_results) ? result.op_results : result?.game?.evidence?.operations;
  if (!Array.isArray(rows)) return null;
  const accepted = rows.filter((row) => row?.success === true).map((row) => Number(row.index));
  const refused = rows.filter((row) => row?.success === false).map((row) => Number(row.index));
  if (!accepted.length || !refused.length) return null;
  if (chunk?.purpose !== 'first_clear_build' || !Array.isArray(chunk.op_groups)) {
    // one transaction, several game operations, each with its own verdict -- the accepted ones are cleared
    // as applied and only the refused ones are marked refused. If an accepted and a refused operation touch the same
    // staged move (same plot and direction, or the same worksite build) the split is ambiguous: keep the whole chunk.
    const subset = (indexes) => ({ ...chunk, operations: indexes.map((index) => chunk?.operations?.[index]).filter(Boolean) });
    const acceptedChunk = subset(accepted);
    const refusedChunk = subset(refused);
    const refusedKeys = chunkDeedDirections(refusedChunk);
    const refusedBuilds = chunkWorksiteBuilds(refusedChunk);
    const overlap = [...chunkDeedDirections(acceptedChunk)].some((key) => refusedKeys.has(key))
      || [...chunkWorksiteBuilds(acceptedChunk).keys()].some((deedUid) => refusedBuilds.has(deedUid));
    return overlap ? null : { accepted: acceptedChunk, refused: refusedChunk };
  }
  const select = (indexes) => ({
    ...chunk,
    operations: indexes.map((index) => chunk.operations[index]).filter(Boolean),
    stage_groups: [...new Set(indexes.flatMap((index) => {
      const groups = chunk.op_groups[index];
      return Array.isArray(groups) ? groups : [groups];
    }).filter((group) => group && group !== 'worksite'))],
  });
  return { accepted: select(accepted), refused: select(refused) };
}

function markGameRefusedChunk(review, chunk, txId, reason) {
  const entries = entriesForChunk(review, chunk);
  forgetInFlightRecord(txId, { removeStage: false });
  entries.forEach((entry) => {
    const parent = restakeParent(entry._restake);
    if (parent) {
      delete parent._unstake_tx; // a refused unstake can be signed again -- after Undo or restaging
      parent.refused_at = parent.refused_at || new Date().toISOString(); // phase 3: its move is set aside too
      parent.refusal_reason = reason;
    }
    entry.refused_at = entry.refused_at || new Date().toISOString();
    entry.refusal_reason = reason;
    recordChainUnstake(entry, 'refused', txId);
    entry.refused_tx_id = txId;
    entry.txId = null;
    delete entry.queued_at;
  });
  const builds = chunkWorksiteBuilds(chunk);
  (review?.stagedWorksitesSnapshot || []).forEach((build) => {
    if (builds.get(String(build.deed_uid)) !== build.target) return;
    const current = CERES_STAGED_WORKSITES.get(build.deed_uid);
    if (current?.target !== build.target) return;
    delete current.txId;
    delete current.broadcast_at;
    delete current.queued_at;
    current.blockedReason = reason;
  });
  renderCheckoutSurface();
}

function scheduleCheckoutVerificationPoll(chunk, stateEl, txId, review) {
  const current = (stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl)) || { entryId: chunk?.entryId, pollStartedAt: chunk?.pollStartedAt };
  const receipt = publishOperationReceipt({
    entryId: current.entryId || chunk?.entryId || operationEntryId(),
    family: 'checkout',
    context: { scope: review?.scope || 'portfolio' },
    operations: chunk?.operations || [],
    txId,
    attempt: chunk?.verificationAttempt,
    pollStartedAt: current.pollStartedAt || Date.now(),
  });
  chunk.entryId = receipt.entryId;
  chunk.pollStartedAt = receipt.pollStartedAt;
  if (stateEl) CHECKOUT_VERIFY_TARGETS.set(stateEl, { chunk, review, txId, entryId: receipt.entryId, pollStartedAt: receipt.pollStartedAt });
  return scheduleOperationPoll(receipt, (updated) => verifyBroadcast(chunk, stateEl, updated.txId, review));
}

function isStakeChangeBatch(chunk) {
  const operations = Array.isArray(chunk?.operations) ? chunk.operations : [];
  return operations.length > 0 && operations.every((operation) => (
    Array.isArray(operation) && operation[0] === 'custom_json' && operation[1]?.id === 'sm_stake_change'
  ));
}

export async function verifyBroadcast(chunk, stateEl, txId, review) {
  // defect 2 -- `verification_attempt` starts at 1 on the first check (right
  // after Keychain returns a tx id) and increments on every manual "Check again", so
  // the server can tell "still propagating" from "exhausted its escalation threshold"
  // (fides_land/verify.py's PENDING_ESCALATION_ATTEMPTS). Kept on the chunk object
  // itself -- its lifetime already matches one review/broadcast sequence.
  chunk.verificationAttempt = (chunk.verificationAttempt || 0) + 1;
  const target = (stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl)) || { entryId: chunk?.entryId, pollStartedAt: chunk?.pollStartedAt };
  chunk.entryId = target.entryId;
  if (stateEl) CHECKOUT_VERIFY_TARGETS.set(stateEl, { chunk, review, txId, entryId: target.entryId, pollStartedAt: target.pollStartedAt });
  markBroadcastedChunk(review, chunk, txId);
  setVerificationNotice(stateEl, { code: 'verification_waiting', kind: 'verification', severity: 'info', blocking: 'repeat', reason: 'Keychain accepted; checking the chain.' });
  const report = (notice, options = {}) => {
    if (stateEl) setVerificationNotices(stateEl, notice ? [notice] : [], options.opResults || []);
    const facts = entryFacts(entriesForChunk(review, chunk));
    setConfirmationBannerState(
      confirmationHeadline(notice),
      noticeMessage(notice),
      toneForNotice(notice),
      {
        state: stateForNotice(notice),
        phase: notice?.code === 'verification_pending_escalated' ? 'pending-escalated'
          : notice?.code === 'verification_pending' ? 'pending'
          : ['verification_could_not_verify', 'verification_lookup_failed', 'verification_not_found', 'verification_domain_check_failed'].includes(notice?.code) ? 'could-not-verify'
              : notice?.code === 'verification_game_refused' ? 'refused'
                : ['verification_mismatch', 'verification_not_applied'].includes(notice?.code) ? 'mismatch'
                : notice?.code === 'verification_confirmed' ? 'confirmed' : undefined,
        // the phase mark spins whenever Ceres is still checking by itself.
        waiting: options.waiting ?? notice?.code === 'verification_pending',
        aria: notice?.aria || NOTICE_CATALOG[notice?.code]?.aria || 'polite',
        txId,
        ...facts,
        runId: chunk?.runId || review?.runId || null,
        runKind: chunk?.runKind || review?.runKind || null,
        action: options.action === false ? null : notice?.action || null,
        retry: options.retry || null,
        opResults: options.opResults || [],
      },
    );
  };
  try {
    const response = await fetch(playerQueryAppend('/api/op-verify'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tx_id: txId, operations: chunk?.operations || [], verification_attempt: chunk?.verificationAttempt }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `verification HTTP ${response.status}`);
    chunk.receiptEntries = chunk.receiptEntries || review?.stagedSnapshot || [];
    const opResults = perOperationResults(result, chunk);
    if (result.state === 'domain-confirmed') {
      balancesChanged({ player: CURRENT_PLAYER || BOARD?.player, assets: ['CARDS'], id: `operations:${txId}` });
      const successNotice = result.notices?.length ? result.notices[0] : verificationNoticeForResult(result);
      setVerificationNotices(stateEl, result.notices?.length ? result.notices : [successNotice], opResults);
      const current = stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl);
      stopOperationPoll(current?.entryId || chunk?.entryId);
      // INVARIANT: only domain-confirmed may clear the matching staged entries.
      clearConfirmedChunkEntries(review, chunk);
      if (stateEl) CHECKOUT_VERIFY_TARGETS.delete(stateEl);
      for (const refreshedDeed of result.refreshed_deeds || []) refreshConfirmedPlot(refreshedDeed);
      report(successNotice, { action: false, opResults });
      if ((result.refresh_errors || []).length) {
        setConfirmationBannerState(
          noticeMessage(catalogNotice('verification_refresh_failed')), // one row -- the message already says it is confirmed
          '',
          'caution',
          {
            state: 'is-confirmed-stale',
            phase: 'confirmed-partial',
            aria: 'polite',
            txId,
            action: { id: 'refresh_confirmation', label: 'Re-read' },
            retry: () => Promise.all((result.refresh_errors || []).map((item) => refreshConfirmedPlot(item.deed_uid))),
          },
        );
      }
      return result;
    }
    if (result.state === 'game-refused') {
      const current = stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl);
      stopOperationPoll(current?.entryId || chunk?.entryId);
      if (current?.entryId) {
        publishOperationReceipt({
          ...OPERATION_RECEIPTS.get(current.entryId), phase: 'failed', attempt: chunk.verificationAttempt,
          lastState: result.state, result, failureDetail: gameRefusalReason(result), nextPollAt: null,
        });
      }
      const split = firstClearOutcomeChunks(result, chunk);
      // split only when the accepted operations' own staged moves are found; never clear refused moves by transaction id.
      const opOutcomes = split && (chunk?.purpose === 'first_clear_build' || entriesForChunk(review, split.accepted).length) ? split : null;
      if (opOutcomes) clearConfirmedChunkEntries(review, opOutcomes.accepted);
      markGameRefusedChunk(review, opOutcomes?.refused || chunk, txId, gameRefusalReason(result));
      if (stateEl) CHECKOUT_VERIFY_TARGETS.delete(stateEl);
      report(result.notices?.length ? result.notices[0] : verificationNoticeForResult(result), { action: false, opResults });
      const error = new Error(gameRefusalReason(result));
      error.noticeShown = true;
      throw error;
    }
    if (result.state === 'domain-mismatch') {
      const current = stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl);
      stopOperationPoll(current?.entryId || chunk?.entryId);
      clearFailedChunkMarkers(review, chunk);
      if (stateEl) CHECKOUT_VERIFY_TARGETS.delete(stateEl);
      const error = new Error('The game state does not match the broadcast operations.');
      error.noticeShown = true;
      report(result.notices?.length ? result.notices[0] : verificationNoticeForResult(result), { opResults });
      throw error;
    }
    if (result.state === 'domain-not-applied') {
      const current = stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl);
      stopOperationPoll(current?.entryId || chunk?.entryId);
      clearFailedChunkMarkers(review, chunk);
      if (stateEl) CHECKOUT_VERIFY_TARGETS.delete(stateEl);
      const notice = result.notices?.length ? result.notices[0] : verificationNoticeForResult(result);
      report(notice, { action: false, opResults });
      for (const refreshedDeed of result.refreshed_deeds || []) refreshConfirmedPlot(refreshedDeed);
      const error = new Error(noticeMessage(notice));
      error.noticeShown = true;
      throw error;
    }
    // defect 1 -- `domain-pending`/`domain-could-not-verify` AND any state
    // string this client does not otherwise recognise all land here. routes_op_preview.py's
    // own `op_verify` docstring states the contract verbatim: "a caller must treat any
    // string it does not recognise as not confirmed yet -- check again, never as
    // failure -- new states may be added here over time." This used to end in
    // `throw new Error('Unexpected verification verdict: ...')`, which landed in
    // confirmOperationBroadcast's catch and called markUnsentChunksAborted -- abandoning
    // every chunk of a partially-broadcast batch on a verdict the backend explicitly
    // said was not a failure. Mirrors worksiteVerdictForState's sibling behaviour
    // everything but domain-confirmed/domain-mismatch/domain-not-applied is neutral
    // "not confirmed yet".
    // MUTATION: restoring the old throw for an unrecognised (non-pending
    // could-not-verify) state makes test_unrecognised_verdict_does_not_abort_the_batch
    // go red -- the remaining chunk stops broadcasting.
    const notices = result.notices?.length ? result.notices : [verificationNoticeForResult(result)];
    const escalated = notices.some((notice) => notice?.code === 'verification_pending_escalated');
    let pollingPaused = escalated && !isStakeChangeBatch(chunk);
    if (!pollingPaused) {
      if (typeof scheduleOperationPoll === 'function' && typeof publishOperationReceipt === 'function') {
        pollingPaused = scheduleCheckoutVerificationPoll(chunk, stateEl, txId, review) === false;
      }
    } else {
      stopOperationPoll((stateEl && CHECKOUT_VERIFY_TARGETS.get(stateEl))?.entryId || chunk?.entryId);
    }
    let pendingNotice = result.notices?.length ? result.notices[0] : verificationNoticeForResult(result);
    if (pollingPaused) pendingNotice = {
      ...pendingNotice,
      message: 'Not confirmed yet. Automatic checks have paused. Check again to read the result without sending another transaction.',
      action: { id: 'retry_verification', label: 'Check again' },
    };
    if (stateEl) setVerificationNotices(stateEl, [pendingNotice], opResults);
    report(pendingNotice, { opResults, waiting: !pollingPaused, retry: pendingNotice?.action ? () => verifyBroadcast(chunk, stateEl, txId, review) : null });
    return result;
  } catch (error) {
    if (error?.noticeShown) throw error;
    // a check that
    // could not run (network error, Ceres restarting, a failed read) used to stop every further check, so a transaction the
    // game had accepted stayed orange for good. It now hands over to the same automatic schedule as "not confirmed yet"
    // the first check that reads the game turns the pill green or red. Only once that schedule is used up does it wait
    // for "Check again".
    const retrying = typeof scheduleOperationPoll === 'function' && typeof publishOperationReceipt === 'function' && Boolean(txId)
      && scheduleCheckoutVerificationPoll(chunk, stateEl, txId, review) !== false;
    report({
      code: 'verification_domain_check_failed', kind: 'verification', severity: 'caution', blocking: 'repeat', reason: String(error), evidence: { reason: String(error) },
      ...(retrying ? { message: 'Could not check the game state yet. Ceres checks again automatically.' } : {}),
      action: { id: 'retry_verification', label: 'Check again' },
    }, { waiting: retrying, retry: () => verifyBroadcast(chunk, stateEl, txId, review) });
    const failure = error instanceof Error ? error : new Error(String(error));
    failure.noticeShown = true;
    throw failure;
  }
}

export function publishOperationReceipt(receipt) {
  if (!receipt?.entryId) return null;
  const current = { ...OPERATION_RECEIPTS.get(receipt.entryId), ...receipt };
  OPERATION_RECEIPTS.set(receipt.entryId, current);
  return current;
}

function operationEntryId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `ceres-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function scheduleOperationPoll(receipt, verify) {
  if (!receipt?.entryId || !receipt.txId || !verify) return;
  const current = OPERATION_POLLERS.get(receipt.entryId) || {
    index: 0, timer: null, startedAt: receipt.pollStartedAt || Date.now(), nextPollAt: null,
  };
  if (current.timer != null) return true;
  if (current.index >= OPERATION_POLL_SCHEDULE_MS.length) {
    publishOperationReceipt({ ...receipt, pollingPaused: true, nextPollAt: null });
    return false;
  }
  const nextAt = current.startedAt + OPERATION_POLL_SCHEDULE_MS[current.index];
  current.index += 1;
  current.nextPollAt = nextAt;
  OPERATION_POLLERS.set(receipt.entryId, current);
  publishOperationReceipt({ ...receipt, nextPollAt: nextAt });
  current.timer = setTimeout(async () => {
    current.timer = null;
    const updated = publishOperationReceipt({ ...receipt, nextPollAt: null });
    await verify(updated).catch(() => {});
  }, Math.max(0, nextAt - Date.now()));
  return true;
}

export function stopOperationPoll(entryId) {
  const poll = OPERATION_POLLERS.get(entryId);
  if (poll?.timer != null) clearTimeout(poll.timer);
  OPERATION_POLLERS.delete(entryId);
}

export function clearOperationPollTimer(entryId) {
  const poll = OPERATION_POLLERS.get(entryId);
  if (!poll) return;
  if (poll.timer != null) clearTimeout(poll.timer);
  poll.timer = null;
  poll.nextPollAt = null;
  const receipt = OPERATION_RECEIPTS.get(entryId);
  if (receipt) publishOperationReceipt({ ...receipt, nextPollAt: null });
}

export async function beginSharedOperation({ account, operations, family, context, onResponse }) {
  let receipt = publishOperationReceipt({
    entryId: operationEntryId(), family, context, operations, phase: 'working', txId: null, attempt: 0, pollStartedAt: Date.now(),
  });
  try {
    const broadcast = await requestBroadcastThroughGate(account, operations, (response) => {
      const txId = response?.result?.id || response?.result?.tx_id || response?.tx_id;
      if (response?.success && txId) receipt = publishOperationReceipt({ ...receipt, phase: 'in_flight', txId });
      onResponse?.(response);
    });
    return publishOperationReceipt({ ...receipt, phase: 'in_flight', txId: broadcast.txId, inclusionBlock: broadcast.blockNum });
  } catch (error) {
    publishOperationReceipt({ ...receipt, phase: error.broadcasted === false ? 'failed' : 'in_flight', txId: error.txId || receipt.txId, failureDetail: String(error) });
    throw error;
  }
}

function markUnsentChunksAborted(stateEls, firstUnsentIndex, failedIndex) {
  if (CERES_BATCH) {
    CERES_BATCH.lines.slice(firstUnsentIndex).forEach((line) => Object.assign(line, { state: 'is-aborted', text: 'Not sent — signing stopped' }));
    renderConfirmationBanner();
  }
  for (let index = firstUnsentIndex; index < stateEls.length; index += 1) {
    setVerificationNotice(stateEls[index], {
      code: 'verification_aborted', kind: 'verification', severity: 'caution', blocking: 'repeat',
      message: `not broadcast — aborted after chunk ${failedIndex + 1} failed`,
    });
  }
}

// before signing, make sure the reviewed preview was built on the newest
// capture. If one landed since, re-prepare on it: identical operations proceed to
// signing; changed ones are shown for re-confirmation instead of being signed.
async function freshReviewForSigning(target, review) {
  let status = null;
  try {
    status = await (await fetch(playerQueryAppend('/api/capture/status'))).json();
  } catch (_) {
    stageToast('Current plot data could not be checked. Try signing again after the connection recovers.', true);
    return null;
  }
  const latest = status?.latest_snapshot?.generated_at;
  const stale = status?.state === 'running'
    || (latest && review.captureStamp && Date.parse(latest) > Date.parse(review.captureStamp));
  const before = previewSignature(review.payload);
  if (stale) await adoptLatestCapture();
  await requestOperationPreview(target);
  const next = CERES_PREVIEWS.get(previewKey(target));
  if (!next?.payload) return null;
  if (previewSignature(next.payload) !== before) {
    next.refreshedNotice = true;
    if (target?.scope === 'portfolio') renderCheckoutSurface();
    else updateMountedRow(target.deed_uid);
    return null;
  }
  return next;
}

export async function confirmOperationBroadcast(target, { runId = null, runKind = 'plan' } = {}) {
  const portfolio = target?.scope === 'portfolio';
  let review = CERES_PREVIEWS.get(previewKey(target));
  if (!review?.payload || !walletReady(review.payload.account)) {
    stageToast('Connect a wallet before signing.', true);
    return;
  }
  if ((review.stagedSnapshot || []).some((entry) => entry.txId && (entry.broadcast_at || entry.queued_at))) {
    stageToast(noticeMessage(catalogNotice('verification_in_flight_refusal')), true);
    return;
  }
  // INVARIANT: one review owns one broadcast sequence, so duplicate clicks cannot overlap Keychain popups.
  if (review.broadcasting || review.freshnessCheck || review.broadcastPending) return;
  review.freshnessCheck = true;
  let checked;
  try { checked = await freshReviewForSigning(target, review); } finally { review.freshnessCheck = false; }
  if (!checked) return;
  review = checked;
  review.runId = runId || review.runId || null;
  review.runKind = runKind || review.runKind || null;
  if (review.broadcasting) return;
  review.broadcasting = true;
  review.signingStates = (review.payload.chunks || []).map(() => 'pending');
  review.signingIndex = 0;
  CERES_BROADCAST_ACTIVE += 1;
  if (portfolio) renderCheckoutSurface();
  const checkoutUndo = document.querySelector('#checkout-panel [data-checkout-action="undo-all"]');
  if (checkoutUndo) {
    checkoutUndo.disabled = true;
    checkoutUndo.setAttribute('data-tip', FINISH_TRANSACTION_FIRST);
    checkoutUndo.setAttribute('data-tip-peer', 'checkout_blockers');
    checkoutUndo.setAttribute('data-tip-depth', 'extended');
    checkoutUndo.setAttribute('aria-label', FINISH_TRANSACTION_FIRST);
  }
  const reviewEl = portfolio
    ? document.querySelector('#checkout-panel .op-preview-review')
    : document.querySelector(`.expand-row[data-deed-uid="${target.deed_uid}"] .op-preview-review`);
  const stateEls = [...(reviewEl?.querySelectorAll('.op-state') || [])];
  const chunks = review.payload.chunks || [];
  const broadcastButton = reviewEl?.querySelector('[data-op-action="confirm"]')
    || (portfolio ? document.querySelector('#checkout-panel [data-op-action="confirm"]') : null); // the footer's Sign
  if (broadcastButton) broadcastButton.disabled = true;
  CERES_BATCH = chunks.length > 1 ? { lines: chunks.map((chunk, index) => ({ index, chunk, txId: null, state: 'is-pending', text: 'waiting for wallet' })) } : null;
  const submitted = [];
  try {
    const envelopes = await prepareSigningBatch(review.payload.account, chunks.map(chunk => chunk.operations), 'Posting', { runId: review.runId, runKind: review.runKind });
    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index];
      review.signingIndex = index;
      review.signingStates[index] = 'current';
      if (portfolio) renderCheckoutSurface();
      if (CERES_BATCH) { CERES_BATCH.lines[index].text = 'waiting for wallet'; renderConfirmationBanner(); }
      setVerificationNotice(stateEls[index], { code: 'verification_waiting', kind: 'verification', severity: 'info', blocking: 'repeat', reason: 'Waiting for the wallet response.' });
      try {
        const broadcast = await requestSignatureThroughGate(review.payload.account, chunk.operations, (response) => {
          if (response?.success) {
            if (broadcastButton) broadcastButton.hidden = true;
            const callbackTxId = response.result?.id || response.result?.tx_id || response.tx_id;
            if (callbackTxId) markBroadcastedChunk(review, chunk, callbackTxId);
            if (portfolio && index === chunks.length - 1 && S.CERES_CHECKOUT_OPEN) { S.CERES_CHECKOUT_OPEN = false; renderCheckoutSurface(); }
          }
        }, 'Posting', envelopes?.[index]);
        chunk.inclusionBlock = broadcast.blockNum;
        if (broadcast.queued) markBroadcastedChunk(review, chunk, broadcast.txId, true);
        review.signingStates[index] = 'signed';
        review.signingIndex = index + 1;
        if (portfolio) renderCheckoutSurface();
        submitted.push({ chunk, stateEl: stateEls[index], txId: broadcast.txId, submission: broadcast.submitted });
        if (CERES_BATCH) {
          Object.assign(CERES_BATCH.lines[index], { txId: broadcast.txId, state: broadcast.queued ? 'is-queued' : 'is-sent', text: broadcast.queued ? 'Signed — queued for broadcast' : 'Broadcast — awaiting verification' });
          renderConfirmationBanner();
        }
      } catch (error) {
        review.signingStates[index] = 'attention';
        if (portfolio) renderCheckoutSurface();
        if (error?.inclusionUnobserved || error?.ambiguousBroadcast) {
          review.broadcastPending = true;
          review.pendingBroadcastIds = error.broadcastRequestIds || [];
          let pendingVerification = null;
          const checkPending = error.pendingBroadcast ? () => {
            if (!pendingVerification) {
              pendingVerification = error.pendingBroadcast
                .then((lateBroadcast) => {
                  clearPendingReview(review);
                  chunk.inclusionBlock = lateBroadcast.blockNum;
                  return verifyBroadcast(chunk, stateEls[index], lateBroadcast.txId, review);
                })
                .catch((pendingError) => {
                  clearPendingReview(review);
                  if (pendingError?.retryable && broadcastButton) {
                    broadcastButton.hidden = false;
                    broadcastButton.disabled = false;
                  }
                  if (pendingError?.txId) return verifyBroadcast(chunk, stateEls[index], pendingError.txId, review);
                  throw pendingError;
                });
            }
            return pendingVerification;
          } : error.txId ? () => verifyBroadcast(chunk, stateEls[index], error.txId, review) : null;
          const inclusionNotice = {
            code: 'verification_inclusion_unobserved', kind: 'verification', severity: 'caution', blocking: 'repeat',
            message: error?.keychainTimedOut
              ? 'Check again, never a second broadcast — Keychain has not returned a result, so this request remains pending verification.'
              : 'Check again, never a second broadcast — the transaction was broadcast, but its inclusion could not be observed.',
            aria: 'assertive',
          };
          setVerificationNotice(stateEls[index], inclusionNotice);
          const entries = entriesForChunk(review, chunk);
          const facts = entryFacts(entries);
          setConfirmationBannerState(
            error?.keychainTimedOut ? 'Pending verification' : 'Broadcasted, inclusion not observed',
            inclusionNotice.message,
            'caution',
            {
              state: 'is-unresolved', aria: 'assertive', txId: error.txId || entries.find((entry) => entry?.txId)?.txId || null, ...facts,
              action: checkPending ? { id: 'retry_verification', label: 'Check again' } : null,
              retry: checkPending,
            },
          );
          if (broadcastButton) {
            broadcastButton.hidden = true;
            broadcastButton.disabled = true;
          }
          if (error.pendingBroadcast) checkPending().catch(() => {});
        } else if (error?.pendingWalletRequests) {
          // state the truth and check it ourselves. No action for him, ever.
          const waiting = `Ceres is checking with Hive whether ${error.pendingWalletRequests === 1 ? 'an earlier wallet request' : `${error.pendingWalletRequests} earlier wallet requests`} went through. Nothing new was sent.`;
          review.pendingWalletBlock = {
            message: waiting,
            count: error.pendingWalletRequests,
            requiredCount: error.requiredCustomOperations || 1,
            checking: true,
          };
          const failureNotice = {
            code: 'verification_domain_check_failed', kind: 'verification', severity: 'caution', blocking: 'sign',
            reason: waiting, evidence: { reason: waiting },
          };
          // the page-level pill is fixed at the top of the screen with no timer
          // and no dismiss for this state, so raising it here left a banner hanging over
          // the page for minutes. The inline notice beside Sign says the same thing and
          // blocks nothing.
          setVerificationNotice(stateEls[index], failureNotice);
          autoResolveBlockedWalletRequests(target).catch(() => {});
          if (portfolio) renderCheckoutSurface();
          else updateMountedRow(target.deed_uid);
        } else if (!error?.noticeShown) {
          clearPendingReview(review);
          const failureNotice = {
            code: 'verification_domain_check_failed', kind: 'verification', severity: 'danger', blocking: 'sign',
            reason: String(error), evidence: { reason: String(error) },
            action: { id: 'retry_verification', label: 'Check again' },
          };
          setVerificationNotice(stateEls[index], failureNotice);
          const entries = entriesForChunk(review, chunk);
          const facts = entryFacts(entries);
          setConfirmationBannerState(
            'Broadcast did not start',
            String(error),
            'danger',
            { state: 'is-failed', aria: 'assertive', ...facts, action: null, retry: null },
          );
        }
        const liveReviewEl = portfolio
          ? document.querySelector('#checkout-panel .op-preview-review')
          : document.querySelector(`.expand-row[data-deed-uid="${target.deed_uid}"] .op-preview-review`);
        const liveStateEls = [...(liveReviewEl?.querySelectorAll('.op-state') || [])];
        markUnsentChunksAborted(liveStateEls.length ? liveStateEls : stateEls, index + 1, index);
        if (error?.retryable && broadcastButton) {
          broadcastButton.hidden = false;
          broadcastButton.disabled = false;
        }
        break;
      }
    }
  } finally {
    await Promise.allSettled(submitted.map(async item => {
      try {
        await item.submission;
        markBroadcastedChunk(review, item.chunk, item.txId);
        const result = await verifyBroadcast(item.chunk, item.stateEl, item.txId, review);
        const index = chunks.indexOf(item.chunk);
        if (index !== -1) review.signingStates[index] = result?.state === 'domain-confirmed' ? 'confirmed' : 'signed';
        if (portfolio) renderCheckoutSurface();
        return result;
      } catch (error) {
        const index = chunks.indexOf(item.chunk);
        if (index !== -1) review.signingStates[index] = 'attention';
        if (portfolio) renderCheckoutSurface();
        if (error.broadcasted === false) {
          clearPendingReview(review);
          markGameRefusedChunk(review, item.chunk, item.txId, error.message);
        }
        else review.broadcastPending = true;
        const refused = error.broadcasted === false;
        setConfirmationBannerState(
          refused ? 'Transaction not sent' : 'Transaction queued or awaiting verification',
          error.message,
          refused ? 'danger' : 'caution',
          { state: refused ? 'is-failed' : 'is-unresolved', phase: refused ? 'refused' : 'pending', txId: item.txId },
        );
      }
    }));
    review.broadcasting = false;
    delete review.signingStates;
    delete review.signingIndex;
    CERES_BROADCAST_ACTIVE -= 1;
    if (portfolio) renderCheckoutSurface();
    const undoButton = document.querySelector('#checkout-panel [data-checkout-action="undo-all"]');
    if (undoButton) undoButton.disabled = CERES_BROADCAST_ACTIVE > 0;
  }
}

// runs by itself, never from a control he has to press. Kept exported under
// the old name for the queue tests; the UI calls autoResolveBlockedWalletRequests.
export async function recoverBlockedWalletRequests(target) {
  const key = target?.scope === 'portfolio' ? CERES_PORTFOLIO_KEY : target?.deed_uid;
  const review = CERES_PREVIEWS.get(key);
  if (!review?.pendingWalletBlock) return null;
  review.pendingWalletBlock.checking = true;
  if (target?.scope === 'portfolio') renderCheckoutSurface();
  else updateMountedRow(key);
  let result;
  try {
    result = await recoverPendingBroadcastRequests(review.payload.account, review.pendingWalletBlock.requiredCount);
  } catch (_) {
    result = { blocked: true, remaining: review.pendingWalletBlock.count };
  }
  if (result.blocked) {
    const count = result.remaining || review.pendingWalletBlock.count;
    review.pendingWalletBlock = {
      ...review.pendingWalletBlock,
      checking: true,
      count,
      message: pendingWalletWaitMessage(count, result.retryAfter),
    };
    // inline only. No page-level pill for a state that can last minutes.
  } else {
    delete review.pendingWalletBlock;
    clearPendingReview(review);
    setConfirmationBannerState('Ready to sign', 'The earlier wallet request is settled. Your staged changes are ready to sign.', 'info', { state: 'is-sent' });
  }
  if (target?.scope === 'portfolio') renderCheckoutSurface();
  else updateMountedRow(key);
  return result;
}

// never promise that something clears itself without saying when. Hive cannot
// judge a request that carries no transaction id until its settle window has passed, so
// the server returns the seconds left and this states them plainly.
function pendingWalletWaitMessage(count, retryAfter) {
  const subject = count === 1 ? 'an earlier wallet request' : `${count} earlier wallet requests`;
  const seconds = Number(retryAfter);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return `Ceres is checking with Hive about ${subject}. Nothing new was sent.`;
  }
  const minutes = Math.ceil(seconds / 60);
  const when = new Date(Date.now() + seconds * 1000)
    .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `Hive cannot confirm ${subject} yet: it needs about ${minutes} more ${minutes === 1 ? 'minute' : 'minutes'}. Nothing new was sent. Ceres checks again at ${when} and clears this by itself.`;
}

// keep checking on our own until the earlier request resolves, so the maintainer
// never has to press anything. Bounded: a few slow attempts, then the page-level timer
// in broadcast-gate.js carries on. Only a definitive verdict changes anything.
export async function autoResolveBlockedWalletRequests(target, attempts = 4) {
  const key = target?.scope === 'portfolio' ? CERES_PORTFOLIO_KEY : target?.deed_uid;
  if (CERES_AUTO_RESOLVING.has(key)) return null; // one loop per surface, however often Sign is pressed
  CERES_AUTO_RESOLVING.add(key);
  try {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const review = CERES_PREVIEWS.get(key);
      if (!review?.pendingWalletBlock) return null;
      const result = await recoverBlockedWalletRequests(target);
      if (result && !result.blocked) return result;
      if (attempt + 1 < attempts) await new Promise((resolve) => { setTimeout(resolve, 5000); });
    }
  } finally {
    CERES_AUTO_RESOLVING.delete(key);
  }
  // Still unresolved after the quick attempts: the page-level timer in broadcast-gate.js
  // keeps checking, and its 'ceres:pending-requests-resolved' event clears this surface.
  return null;
}
