// claim SPS rewards from the wallet's SPS tab. One Posting-signed `sm_claim_rewards`, prepared by
// api/wallet/claim/preview, signed by the wallet through the shared broadcast gate, confirmed only by the
// game's own result via /api/wallet/claim/verify. / : the pending intent lives only in this
// page's memory -- never stored -- so an old claim can never resurface; rows saved by earlier versions are
// removed silently. : the known transaction is checked automatically in the original rewards card
// no transaction ID or manual check control is exposed. Confirmation quietly re-reads the SPS wallet total.
import { balancesChanged } from './balances-bus.js';
import { esc } from './format.js';
import { tokenIcon } from './harvest-state.js';
import { walletControlsHtml, walletReady, walletSignLabelHtml, walletWaitHtml } from './wallet.js';
import { requestBroadcastThroughGate } from './broadcast-gate.js';

const fmt = value => Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
const key = player => `ceres:wallet-claim:pending:${player}`;
const button = (action, label, primary = false, disabled = false) => `<button type="button" class="ceres-btn ceres-btn--${primary ? 'primary' : 'secondary'}" data-control-density="touch" data-dec="claim:${action}"${disabled ? ' disabled' : ''}>${label}</button>`;
const spsIcon = () => `<img src="${esc(tokenIcon('SPS'))}" alt="SPS" class="wallet-token-icon">`;

const pendingByPlayer = new Map();

export function loadClaimPending(player) {
  try { localStorage.removeItem(key(player)); } catch (_) { /* storage unavailable: nothing to purge */ }
  return pendingByPlayer.get(player) || null;
}
function savePending(D, value) {
  if (value) pendingByPlayer.set(D.player, value);
  else pendingByPlayer.delete(D.player);
  D.claim.pending = value;
}

async function api(D, path, body) {
  const response = await fetch(`/api/wallet/claim/${path}?player=${encodeURIComponent(D.player)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    ...(path === 'verify' && typeof AbortSignal !== 'undefined' ? { signal: AbortSignal.timeout(8000) } : {}),
  });
  const result = await response.json();
  if (!response.ok) { const error = new Error(typeof result.detail === 'string' ? result.detail : 'Claim request failed. Please retry.'); error.status = response.status; throw error; }
  return result;
}

// (b): claim buttons must also freeze while the wallet drawer itself is loading (D.busy)
// not only while the claim's own request is in flight (claim.busy). Exported as a pure helper so
// the disabled-flag logic can be slice-tested without loading the wallet/broadcast-gate stack.
export function claimControlsDisabled(D) {
  return !!(D.claim.busy || D.busy);
}

export function claimHtml(D) {
  const claim = D.claim;
  const rewards = D.rewards;
  const disabled = claimControlsDisabled(D);
  if (claim.pending) {
    const p = claim.pending;
    const confirmed = p.status === 'confirmed', failed = p.status === 'failed';
    const phase = confirmed ? 'done' : failed ? 'failed' : D.signing && p.phase !== 'sent' ? 'asked' : p.phase || 'checking';
    const message = confirmed ? 'The game has paid these rewards to your SPS balance.'
      : failed ? esc(p.message || 'The game rejected this claim.')
      : phase === 'asked' ? walletWaitHtml(D.player, 'Posting', { since: p.asked_at || Date.now() })
      : phase === 'sent' ? 'Claim sent. Checking with the game now…'
      : phase === 'retried' ? 'Still checking with the game. Ceres retries automatically; nothing is signed again.'
      : 'Checking with the game. This updates automatically.';
    return `<div class="wallet-claim-flow is-${phase}" role="status" aria-live="polite">
      <h4>${confirmed ? 'SPS rewards claimed' : failed ? 'Claim rejected' : phase === 'asked' ? 'Check Keychain for approval' : phase === 'sent' ? 'Claim sent' : phase === 'retried' ? 'Checking again' : 'Confirming claim'}</h4>
      ${confirmed ? `<div class="dec-receipt-amount">${spsIcon()}<div><span>Claimed</span><strong>${fmt(p.total)} <small>SPS</small></strong></div></div><ul class="wallet-regions wallet-rewards">${(p.claimed || []).map(r => `<li><span class="wallet-reward-label">${esc(r.label)}</span><span></span><span class="wallet-figure">${fmt(r.balance)}${spsIcon()}</span></li>`).join('')}</ul>` : ''}
      ${phase === 'asked' ? message : `<p>${message}</p>`}
      ${confirmed ? `<div class="dec-receipt-actions">${button('done', 'Done', true, disabled)}</div>` : failed ? `<div class="dec-receipt-actions">${button('retry', 'Review claim again', true, disabled)}</div>` : ''}</div>`;
  }
  if (claim.review) {
    const r = claim.review;
    return `<div class="wallet-claim-flow is-review"><h4>Claim SPS rewards</h4>
      <div class="dec-receipt-amount">${spsIcon()}<div><span>You will receive</span><strong>${fmt(r.total)} <small>SPS</small></strong></div></div>
      <ul class="wallet-regions wallet-rewards">${r.rows.filter(x => Number(x.balance) > 0).map(x => `<li><span class="wallet-reward-label">${esc(x.label)}</span><span class="wallet-region-plots">${x.source === 'land' ? 'land' : 'battle'}</span><span class="wallet-figure">${fmt(x.balance)}${spsIcon()}</span></li>`).join('')}</ul>
      <p class="wallet-meta">One transaction claims every reward type at once; the game does not split battle and land claims.</p>
      <details><summary>Show Hive operation</summary><pre>${esc(JSON.stringify(r.operations, null, 2))}</pre></details>
      <div class="dec-region-actions">${button('back', 'Back', false, disabled)}<span class="wallet-sign-pair">${walletControlsHtml(D.player)}${button('sign', walletSignLabelHtml('Claim and sign with'), true, disabled || !r.signing_enabled || !walletReady(D.player))}</span></div>
      ${!r.signing_enabled ? '<p>Signing is disabled on this server.</p>' : ''}</div>`;
  }
  const total = Number(rewards?.total || 0);
  const waiting = rewards?.next_claim_at && Date.parse(rewards.next_claim_at) > Date.now();  // one claim per 23 h
  return `<div class="dec-region-actions wallet-claim-actions">${button('review', waiting ? 'Claim SPS rewards (on cooldown)' : 'Claim SPS rewards', true, disabled || !rewards || total <= 0 || waiting)}</div>`;
}

async function check(D, render, account, reload) {
  const generation = D.generation, player = D.player, intent = { ...D.claim.pending };
  const current = () => generation === D.generation && player === D.player && account() === player && D.claim.pending?.nonce === intent.nonce && D.claim.pending?.tx_id === intent.tx_id;
  if (D.claim.checking) return;
  // The broadcast gate normally provides its deterministic id, including on timeout. When it cannot
  // no by-id check is possible; re-read the live claim instead of asking for an id or signing again.
  if (!intent.tx_id) {
    savePending(D, { ...intent, phase: 'retried' }); render();
    await new Promise(resolve => setTimeout(resolve, 2500));
    if (current()) { await reload(); savePending(D, null); render(); }
    return;
  }
  D.claim.checking = true; render();
  try {
    for (const delay of [0, 2000, 3000, 5000]) {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (!current()) return;
      try {
        const result = await api(D, 'verify', { tx_id: intent.tx_id, nonce: intent.nonce });
        if (!current()) return;
        if (result.status === 'confirmed' || result.status === 'failed') {
          savePending(D, { ...intent, ...result, phase: result.status === 'confirmed' ? 'done' : 'failed' }); render();
          if (result.status === 'confirmed') balancesChanged({ player: D.player, assets: ['SPS'], id: `claim:${intent.tx_id}` });
          return;
        }
        // `not_found` / `pending`: the game has not answered yet -- keep waiting quietly.
      } catch (error) {
        if (!current()) return;
        // A lookup error is transient here. Keep the known transaction and retry the same id.
      }
    }
  } finally { if (generation === D.generation) D.claim.checking = false; }
  if (current()) {
    savePending(D, { ...D.claim.pending, phase: 'retried' }); render();
    D.claim.retryTimer = setTimeout(() => { D.claim.retryTimer = null; if (current()) check(D, render, account, reload); }, 3000);
  }
}

// Returns true when the action was a claim action (handled here).
export async function claimAction(D, name, render, account, reload) {
  const claim = D.claim;
  if (claim.busy) return true;
  D.error = '';
  if (name === 'back') { claim.review = null; render(); return true; }
  if (name === 'done') { savePending(D, null); claim.review = null; render(); return true; }
  if (name === 'retry' && claim.pending?.status === 'failed') { savePending(D, null); claim.review = null; render(); await reload(); return true; }
  const generation = D.generation;
  claim.busy = true; render();
  try {
    if (name === 'review') { const preview = await api(D, 'preview'); if (generation === D.generation && account() === D.player) claim.review = preview; }
    if (name === 'verify') await check(D, render, account, reload);  // internal resume only; no manual control
    if (name === 'sign') {
      const fresh = await api(D, 'preview');
      if (account() !== D.player) throw new Error('Player changed. Close and reopen the wallet.');
      if (!fresh.signing_enabled || !walletReady(D.player)) throw new Error('Connect a wallet before signing.');
      if (fresh.total !== claim.review.total) { claim.review = fresh; throw new Error('The claimable amount changed. Review it before signing.'); }
      savePending(D, { nonce: fresh.nonce, total: fresh.total, status: 'pending', phase: 'asked', asked_at: Date.now() });
      claim.review = null; D.signing = true; render();
      try {
        const result = await requestBroadcastThroughGate(D.player, fresh.operations, response => {
          const tx = response?.result?.id || response?.result?.tx_id || response?.tx_id;
          if (typeof tx === 'string' && /^[a-f0-9]{40}$/i.test(tx)) { savePending(D, { ...D.claim.pending, tx_id: tx, phase: 'sent' }); render(); }
        });
        savePending(D, { ...D.claim.pending, tx_id: result.txId, phase: 'sent' }); render();
        D.signing = false;
        await check(D, render, account, reload);
      } catch (error) {
        if (typeof error.txId === 'string' && /^[a-f0-9]{40}$/i.test(error.txId) && D.claim.pending) {
          savePending(D, { ...D.claim.pending, tx_id: error.txId, phase: 'checking' });
          D.signing = false; await check(D, render, account, reload);
        } else if (error.broadcasted === false || (error.retryable && !error.ambiguousBroadcast)) {
          savePending(D, null); claim.review = fresh;
        } else if (D.claim.pending) {
          D.signing = false; await check(D, render, account, reload);
        }
      }
    }
  } catch (error) { if (generation === D.generation) D.error = error.message; }
  finally { claim.busy = false; D.signing = false; if (generation === D.generation) render(); }
  return true;
}
