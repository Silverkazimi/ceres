import { prepareSigningBatch, requestSignatureThroughGate } from './broadcast-gate.js';
import { signedQueueRows } from './transaction-queue.js';
import { walletCanSignTransaction, walletReady } from './wallet.js';
import { routingApprovalCounts, routingWindowHtml } from './routing-window.js';
import { confirmAction, confirmationState } from './shared-confirmer.js';

const ROOT = '/api/harvest/routing/v2/';
const FINISHED = new Set(['confirmed', 'refused', 'skipped', 'stopped']);
const SEEN_RUNS = new Set();
// a bundle is confirmed by the shared confirmer (shared-confirmer.js) -- its schedule, its own-history finder
// for a missing id () and its one read at Hive expiry. This module hands it one read (`readBundle`) and renders
// the per-operation states it gets back. No own pause, poll count, probe cap, call budget or manual check (IN-0706).

async function request(player, path, body, method = 'POST', token = null) {
  const headers = { ...(token ? { 'X-Run-Token': token } : {}) };   // the run's own token; the server opens nothing without it
  const response = await fetch(`${ROOT}${path}?player=${encodeURIComponent(player)}`, method === 'GET' ? { method, headers } : {
    method, headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || `Routing request failed (${response.status})`);
  return value;
}

const tokenKey = player => `ceres:routing-v2-token:${player}`;

function queueClaims(player) {
  const claims = new Set();
  for (const row of signedQueueRows(player)) {
    // A request the wallet refused before signing holds no signed copy; it can never reach the chain.
    if (row.state === 'cancelled' && !row.signedTransaction) continue;
    for (const [, body] of row.transaction?.operations || []) {
      try { claims.add(String(JSON.parse(body.json).n)); } catch (_) { throw new Error('Saved routing queue cannot be checked.'); }
    }
  }
  return claims;
}

function memberClaims(player) {
  const value = JSON.parse(localStorage.getItem(`ceres:routing-v2-members:${player}`) || '[]');
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) throw new Error('Saved routing claims cannot be checked.');
  return new Set(value);
}

function reserveMembers(player, nonces) {
  const used = memberClaims(player);
  const queued = queueClaims(player);
  if (nonces.some(nonce => used.has(nonce) || queued.has(nonce))) throw new Error('A route share already has a wallet claim; Ceres is confirming it. No second signature is allowed.');
  nonces.forEach(nonce => used.add(nonce));
  localStorage.setItem(`ceres:routing-v2-members:${player}`, JSON.stringify([...used]));
}

function unreserveMembers(player, nonces) {
  const used = memberClaims(player);
  const queued = queueClaims(player);
  nonces.filter(nonce => !queued.has(nonce)).forEach(nonce => used.delete(nonce));
  localStorage.setItem(`ceres:routing-v2-members:${player}`, JSON.stringify([...used]));
  return nonces.every(nonce => !queued.has(nonce));
}

export function createRoutingRun({ player, onRender, isCurrent, api = request,
  sign = requestSignatureThroughGate, prepare = prepareSigningBatch } = {}) {
  const S = { record: null, busy: false, error: '', runId: null, token: null, active: true, pendingReview: null };
  const call = (name, body) => api(player, name, body, 'POST', S.token);   // every call of this run carries the run's own token
  const key = `ceres:routing-v2-run:${player}`;
  const excludedKey = `ceres:routing-v2-excluded:${player}`;
  const excluded = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(excludedKey) || '[]');
    if (Array.isArray(saved)) saved.filter(item => typeof item === 'string').forEach(item => excluded.add(item));
  } catch (_) {}
  // the wallet half of a signing failure happens in the browser; send its reason and size to the Ceres log.
  // Allowlisted fields only (no operation bodies, no signed bytes); never throws and never blocks the screen.
  const report = (stage, error, extra = {}) => {
    try {
      Promise.resolve(api(player, 'client-report', { stage, run_id: S.runId, error_name: error?.name, error: String(error?.message || error || ''),
        declined: Boolean(error?.declined || error?.signingRejected), broadcasted: typeof error?.broadcasted === 'boolean' ? error.broadcasted : undefined,
        retryable: typeof error?.retryable === 'boolean' ? error.retryable : undefined, has_tx_id: Boolean(error?.txId), ...extra })).catch(() => {});
    } catch (_) {}
  };
  const notify = () => { if (isCurrent?.()) onRender?.(); };
  const settled = record => ['complete', 'stopped'].includes(record?.state) && !Object.values(record.bundles || {}).some(status => ['claimed', 'attached', 'checking', 'outcome_unknown'].includes(status.state));
  // the run lives in this page. The server file exists only while the run is open; the pointer below exists only so a LATER page can delete it.
  const update = record => { S.record = record; S.runId = record.run_id; SEEN_RUNS.add(record.run_id); try { if (settled(record)) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify({ run_id: record.run_id, token: S.token })); } catch (_) {} notify(); return record; }; // a full or blocked storage never stops the screen
  const bundleStatus = bundle => S.record.bundles[bundle.id];
  const current = () => S.record?.manifest?.bundles.find(bundle => !FINISHED.has(bundleStatus(bundle).state));

  // (maintainer: the one-time run is deleted on done, failed or a page refresh): a run of an earlier page is NEVER resumed; it is deleted on the server (what may
  // have been sent is kept there as a tiny safety record) and forgotten here.
  async function load() {
    let raw = null;
    try { raw = localStorage.getItem(key); } catch (_) { raw = null; }
    if (!raw || S.record) return S.record || null;
    let runId = null, token = null;
    try { const parsed = JSON.parse(raw); runId = parsed?.run_id; token = parsed?.token || null; } catch (_) { runId = raw; }   // a pointer of an older release holds the bare id
    try { localStorage.removeItem(key); } catch (_) {}
    if (typeof runId !== 'string') return null;
    try { await api(player, 'discard', { run_id: runId }, 'POST', token); } catch (_) { /* the server forgets it at its next start or after the idle lease anyway */ }
    return null;
  }

  async function create(preview) {
    if (S.record?.preview_id === preview.preview_id) return S.record;
    // an earlier run never blocks a new one (the server retires it and keeps only what may have been sent); this page just starts over.
    S.record = null; S.runId = null; S.pendingReview = null; try { localStorage.removeItem(key); } catch (_) {}
    // nothing of an earlier routing is kept or checked: its claim journal goes with it (a new run has new nonces).
    try { localStorage.removeItem(`ceres:routing-v2-members:${player}`); localStorage.removeItem(tokenKey(player)); } catch (_) {}
    excluded.clear();
    try { localStorage.removeItem(excludedKey); } catch (_) {}
    S.token = null;
    const created = await call('run', { preview_id: preview.preview_id, preview_digest: preview.preview_digest });
    S.token = created.run_token || null;   // shown once; kept only by this page (and its pointer, so a later page can delete the run)
    try { if (S.token) localStorage.setItem(tokenKey(player), S.token); } catch (_) {}
    const { run_token: _shownOnce, ...record } = created;
    return update(record);
  }

  // The bundle's state as the shared confirmer's four states, per operation ( design item 4).
  function verdict(bundle) {
    const status = bundleStatus(bundle);
    const perOp = (status.operation_ids || []).map((id, index) => {
      const state = S.record.operations[id]?.state;
      return { index, state: state === 'confirmed' ? 'confirmed' : state === 'refused' ? 'refused'
        : state === 'stopped' ? 'never-sent' : 'pending', ...(S.record.operations[id]?.error ? { error: S.record.operations[id].error } : {}) };
    });
    if (status.state === 'confirmed') return { state: 'confirmed', perOp };
    if (status.state === 'refused') return { state: 'refused', perOp, error: perOp.find(op => op.error)?.error || 'The game refused this routing step.' };
    if (status.state === 'stopped') return { state: 'never-sent', perOp, error: 'Hive has no record of this routing transaction and it has expired. It was not sent.' };
    return { state: 'pending', perOp, message: status.chain === 'included' ? 'On Hive; waiting for the game. Ceres keeps checking; nothing is signed again.'
      : 'Sent; waiting for Hive. Ceres keeps checking; nothing is signed again.' };
  }

  // ONE read for the shared confirmer: attach a newly learned id, then one chain probe until Hive has the transaction
  // then one game verdict per open operation. A paced (429) or failed read throws; the confirmer reads again.
  async function readBundle(bundle, txId) {
    if (!S.active || S.record?.bundles?.[bundle.id] === undefined) return { state: 'pending' };
    let status = bundleStatus(bundle);
    if (!status.tx_id && txId) { update(await call('attach', { run_id: S.runId, bundle_id: bundle.id, tx_id: txId })); status = bundleStatus(bundle); }
    if (FINISHED.has(status.state)) return verdict(bundle);
    if (status.chain !== 'included') {
      update(await call('reconcile', { run_id: S.runId, bundle_id: bundle.id, mode: 'chain' }));
      status = bundleStatus(bundle);
      if (status.chain !== 'included') return verdict(bundle);
    }
    const ids = status.operation_ids || [];
    for (let index = 0; index < ids.length; index += 1) {
      if (FINISHED.has(S.record.operations[ids[index]]?.state)) continue;
      update(await call('reconcile', { run_id: S.runId, bundle_id: bundle.id, mode: 'game', operation_index: index }));
    }
    return verdict(bundle);
  }

  // Confirms one signed bundle, with or without an id; resolves when the chain or the game has the final word.
  async function confirmBundle(bundle, operations, txId = null) {
    S.confirming = bundle.id; S.confirmMessage = ''; notify();
    try {
      const answer = await confirmAction({ account: player, operations, txId,
        check: id => readBundle(bundle, id),
        onUpdate: next => { if (next.state === 'pending') { S.confirmMessage = next.message || ''; notify(); } } });
      if (answer.state === 'never-sent' && !bundleStatus(bundle).tx_id) {
        // Proven absent by the own-history reads at Hive expiry: the server stops the bundle and releases its shares.
        // The server checks its OWN expiry proof for this attempt (fix round 1, MEDIUM-3); the browser only names it.
        update(await call('unsent', { run_id: S.runId, bundle_id: bundle.id, attempt: confirmationState(player, operations)?.attemptId || null }));
      } else if (answer.state === 'never-sent' && !FINISHED.has(bundleStatus(bundle).state)) {
        // The confirmer's own Hive check said absent past expiry; the server's chain probe records the same verdict.
        await readBundle(bundle, bundleStatus(bundle).tx_id).catch(() => {});
      }
      return answer;
    } finally { if (S.confirming === bundle.id) { S.confirming = null; S.confirmMessage = ''; } notify(); }
  }

  async function signCurrent() {
    const bundle = current();
    if (!bundle || (bundleStatus(bundle).state !== 'planned' && !(S.pendingReview && bundleStatus(bundle).state === 'claimed')) || S.busy) return;
    S.busy = true; S.error = ''; notify();
    let claimed = null;
    let walletAsked = false;
    let stage = 'start';
    try {
      if (!walletReady(player)) throw new Error('Connect your Posting wallet before signing.');
      if (!navigator.locks?.request) throw new Error('Safe routing coordination requires browser Web Locks.');
      const bundleMembers = bundle.operations.flatMap(operation => operation.members);
      const excludedRouteIds = bundleMembers
        .filter(member => member.kind === 'sale_dec' && excluded.has(member.route_id))
        .map(member => member.route_id);
      stage = 'claim';
      const handoff = S.pendingReview || await navigator.locks.request(`ceres:routing-v2:${player}`, async () => {
        update(await call('state', { run_id: S.runId }));
        if (bundleStatus(bundle).state !== 'planned') throw new Error('This route bundle was already claimed; Ceres is confirming it.');
        const claim = await call('claim', { run_id: S.runId, bundle_id: bundle.id, excluded_route_ids: excludedRouteIds });
        update(await call('state', { run_id: S.runId }));
        if (claim.skipped) return claim;
        if (claim.authority !== 'Posting') throw new Error('Only Posting harvest routes are released.');
        return claim;
      });
      if (handoff.skipped) return;
      claimed = handoff;
      if (!S.pendingReview && handoff.changed_amount_review?.length) {
        S.pendingReview = handoff;
        notify();
        return;
      }
      await navigator.locks.request(`ceres:routing-v2:${player}`, async () => reserveMembers(player, handoff.member_nonces));
      S.pendingReview = null;
      if (!S.active || !walletReady(player)) throw new Error('Routing window or wallet changed before handoff. No new signature was requested.');
      let prepared = null;
      stage = 'head';
      if (walletCanSignTransaction(player)) {
        const head = await call('head', { run_id: S.runId, bundle_id: bundle.id });
        stage = 'prepare';
        prepared = (await prepare(player, [handoff.operations], 'Posting', {
          runId: `routing-v2-${S.runId}`, runKind: 'harvest', headProperties: head,
        }))?.[0] || null;
        if (!prepared) throw new Error('The Posting wallet cannot prepare this routing bundle.');
      }
      if (!S.active || !walletReady(player)) throw new Error('Routing window or wallet changed before signing. No new signature was requested.');
      let result = null;
      walletAsked = true;
      stage = 'sign';
      // round 4 (aletheia-verdict-r4 3): the server releases this bundle's claim only on its own proof, so the
      // confirmer never says "Starting again is safe" for it (CLAIM_HELD_NEVER_ASKED instead).
      try { result = await sign(player, handoff.operations, null, 'Posting', prepared, { runId: `routing-v2-${S.runId}`, runKind: 'harvest', holdsServerClaim: true }); }
      catch (error) {
        // broadcasted === false: the wallet refused before anything was sent (a prepared envelope may carry an id that never
        // reached the chain), so the claim is released below.
        if (error.broadcasted === false) throw error;
        // Anything else may have been signed: the shared confirmer takes over (with the id, or finds it; ).
        report('sign', error, { bundle_id: bundle.id, wallet_asked: true, confirming: true });
        result = { txId: error.txId || null };
      }
      stage = 'attach';
      if (result?.txId) update(await call('attach', { run_id: S.runId, bundle_id: bundle.id, tx_id: result.txId }));
      stage = 'confirm';
      S.busy = false; notify();   // signing is over; the confirmation shows its own automatic line
      await confirmBundle(bundle, handoff.operations, result?.txId || null);
    } catch (error) {
      report(stage, error, { bundle_id: bundle.id, wallet_asked: walletAsked, operations: claimed?.operations?.[0]?.length,
        bytes: claimed ? JSON.stringify(claimed.operations).length : undefined });
      const unsent = claimed && !bundleStatus(bundle).tx_id && (!walletAsked || error?.broadcasted === false);
      if (unsent) {
        // Nothing was signed: the bundle goes back to "Sign current bundle" and no error is shown.
        try {
          await navigator.locks.request(`ceres:routing-v2:${player}`, async () => {
            // (: a browser whose storage is full cannot write the journal; no wallet was asked, so the server
            // claim is still released (the journal is only needed to prove nothing was signed).
            let free;
            try { free = unreserveMembers(player, claimed.member_nonces); } catch (_) { free = !walletAsked; }
            if (free) update(await call('release', { run_id: S.runId, bundle_id: bundle.id }));
          });
          S.pendingReview = null;
          // Only the maintainer's own Cancel in the wallet needs no text; every other failure (before or after the wallet was asked) keeps its reason ().
          S.error = error?.declined || error?.signingRejected ? '' : error.message || String(error);
        } catch (releaseError) { S.error = releaseError.message || String(releaseError); }
      } else S.error = error.message || String(error);
    }
    finally { S.busy = false; notify(); }
  }

  // one press signs the current bundle and then every following
  // bundle as soon as the previous one is confirmed -- an LP deposit re-priced after its sale is approved in the same
  // flow. It stops only on a wallet refusal (the button stays to continue), an unknown outcome, or the end of the run.
  async function signAll() {
    for (let guard = 0, limit = Object.keys(S.record?.bundles || {}).length + 8; guard < limit && S.active; guard += 1) { // any number of bundles
      const bundle = current();
      if (!bundle) return;
      const before = bundleStatus(bundle).state;
      if (before !== 'planned' && !(S.pendingReview && before === 'claimed')) return;
      await signCurrent();
      if (S.error) return;
      const after = bundleStatus(bundle).state;
      if (after === 'planned') return;                       // declined in the wallet: nothing sent, the button stays
      if (after === 'claimed' && S.pendingReview) continue;   // re-priced LP amounts: approve them straight away
      if (!FINISHED.has(after)) return;                       // still open (page left): the confirmer carries on
    }
  }

  async function stop() {
    if (!S.runId || S.busy) return;
    S.busy = true; notify();
    try { update(await call('stop', { run_id: S.runId })); S.pendingReview = null; }
    catch (error) { S.error = error.message; }
    finally { S.busy = false; notify(); }
  }

  function discardFinished() {
    if (!S.record || !['complete', 'stopped'].includes(S.record.state)
        || Object.values(S.record.bundles).some(status => ['claimed', 'attached', 'checking', 'outcome_unknown'].includes(status.state))) return false;
    S.record = null; S.runId = null; S.pendingReview = null; try { localStorage.removeItem(key); } catch (_) {} notify();
    return true;
  }

  // leaving the page (refresh, close) deletes the run on the server at once (best effort; the idle lease and the next start catch the rest).
  // never while a bundle is being signed. On a phone, HiveAuth/Keychain means leaving the browser for the wallet app, which
  // fires pagehide; discarding then deleted the run and the return from the wallet failed with "Routing run not found".
  const signing = () => S.busy || Object.values(S.record?.bundles || {}).some(status => ['claimed', 'attached', 'checking', 'outcome_unknown'].includes(status.state));
  const onLeave = (event) => {
    if (!S.runId || settled(S.record) || signing() || event?.persisted) return;
    try { navigator.sendBeacon?.(`${ROOT}discard?player=${encodeURIComponent(player)}`, new Blob([JSON.stringify({ run_id: S.runId, run_token: S.token })], { type: 'application/json' })); } catch (_) { /* best effort */ }
  };
  try { window.addEventListener('pagehide', onLeave); } catch (_) { /* no window in tests */ }

  return { get runId() { return S.runId; }, get busy() { return S.busy; }, get record() { return S.record; },
    get counts() { return routingApprovalCounts(S.record?.manifest); },
    html: () => S.record ? routingWindowHtml(S.record, { player, busy: S.busy, error: S.error, excludedRouteIds: excluded, viewBundleId: S.viewBundle,
      confirming: S.confirming, confirmMessage: S.confirmMessage,
      changedAmountReview: S.pendingReview?.changed_amount_review || [] }) : '',
    action: (name, node) => {
      if (name === 'view-bundle') { S.viewBundle = node?.dataset.r3BundleId || null; notify(); return; }
      if (name === 'sign') S.viewBundle = null;
      if (name === 'toggle-member') {
        const routeId = node?.dataset.r3Route;
        if (!routeId || !current() || bundleStatus(current()).state !== 'planned') return;
        if (node.checked) excluded.delete(routeId); else excluded.add(routeId);
        localStorage.setItem(excludedKey, JSON.stringify([...excluded]));
        notify();
        return;
      }
      return ({ sign: signAll, stop })[name]?.();   // no manual check or find control
    },
    load, create, stop, discardFinished, detach: () => { S.active = false; try { window.removeEventListener('pagehide', onLeave); } catch (_) { /* no window */ } },
  };
}
