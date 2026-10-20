import { rebuildCombineForSigning, combineReviewMatches } from './combine-dialog.js';
// explicit plan combines use the shared wallet and known-transaction verifier.
import { withStagedAccountLock } from './account-space.js';
import { BOARD, CURRENT_PLAYER, playerQueryAppend } from './board.js';
import { activeChainPlans, chainPlans, saveChain, completeChainAttempt, persistStagePlans, stagePlansReady } from './stage-plans.js';
import { chainCompleteResult, chainNumber, activeChainInputs, chainCanCombine, chainSignWitness, chainPlacementReady, chainResultMatches } from './combine-chain.js';
import { beginSharedOperation, publishOperationReceipt, scheduleOperationPoll, stopOperationPoll } from './operations.js';
import { CERES_STAGE, applyStageToRow, refreshStageImpactForRow } from './staging.js';
import { updateMountedRow } from './rows.js';
import { renderCheckoutSurface, removeCheckoutStage } from './checkout.js';
import { settleCombineRefresh } from './combine.js';
import { confirmCombineWarning } from './hazard.js';

const PLAN_VERIFYING = new Set();
const PLAN_SIGNING = new Set();
const CHANGED_PLAN = 'The selected cards changed. Review the upgrade plan again. Nothing was sent.';

export function readyPlanCombines() {
  if (document.documentElement.classList?.contains('ceres-lw-le-1279') || !stagePlansReady()) return [];
  return activeChainPlans().filter(([, plan]) => chainCanCombine(plan) && !PLAN_SIGNING.has(plan.id));
}

export async function buildPlanCombine(plan) {
  if (!chainCanCombine(plan)) throw new Error('This plan is not ready to combine.');
  const body = await rebuildCombineForSigning(activeChainInputs(plan).map(input => input.uid), chainSignWitness(plan));
  if (!body.preview?.buildable) throw new Error(CHANGED_PLAN);
  const operation = body.operation;
  const payload = JSON.parse(operation[1]?.json || '{}');
  const wanted = activeChainInputs(plan).map(input => input.uid).sort();
  const sameIds = ids => Array.isArray(ids) && JSON.stringify([...ids].sort()) === JSON.stringify(wanted);
  if (operation[0] !== 'custom_json' || operation[1].id !== 'sm_combine_cards' || !payload.n
      || !sameIds(payload.cards) || !sameIds(body.preview.uids) || body.preview.account !== (CURRENT_PLAYER || BOARD?.player)
      || JSON.stringify(operation[1].required_posting_auths) !== JSON.stringify([body.preview.account])
      || !Array.isArray(operation[1].required_auths) || operation[1].required_auths.length || !sameIds(body.preview.hazard?.rows?.map(row => row.card_id))
      || body.preview.hazard.rows.some(row => row.bcx !== plan.inputs.find(input => input.uid === row.card_id)?.cc)
      || ['bcx', 'base_pp', 'level', 'survivor_uid'].some(key => body.preview.expected?.[key] !== plan.expected[key])) throw new Error(CHANGED_PLAN);
  return body;
}

function planBuildFacts(build) {
  const p = build.preview;
  return JSON.stringify([p.uids, p.predicted_survivor_uid, p.resulting_bcx,
    p.hazard.rows.map(row => [row.card_id, row.bcx, row.level, row.edition, row.foil]), p.hazard.destroyed_uids,
    ['bcx', 'base_pp', 'level'].map(key => p.expected[key])]);
}

export async function preparePlanCombineRun() {
  const player = CURRENT_PLAYER;
  const reviews = [];
  for (const [id, plan] of readyPlanCombines()) {
    try {
      const witness = JSON.stringify(chainSignWitness(plan));
      const build = await buildPlanCombine(plan);
      if (player !== CURRENT_PLAYER || witness !== JSON.stringify(chainSignWitness(chainPlans()[id]))) throw new Error(CHANGED_PLAN);
      reviews.push({ id, plan: JSON.parse(JSON.stringify(plan)), witness, build });
    } catch (_) {
      if (player === CURRENT_PLAYER && chainPlans()[id]) saveChain(id, { ...chainPlans()[id], problem: { code: 'combine_refused', detail: CHANGED_PLAN } });
    }
  }
  if (!reviews.length) return { choice: 'without', reviews: [] };
  const choice = await confirmCombineWarning(reviews);
  return { choice: player === CURRENT_PLAYER ? choice : 'cancel', reviews };
}

export async function signPlanCombine(review) {
  const { id } = review;
  const player = CURRENT_PLAYER;
  const plan = chainPlans()[id];
  if (!plan || PLAN_SIGNING.has(id) || !chainCanCombine(plan) || review.witness !== JSON.stringify(chainSignWitness(plan))) return false;
  PLAN_SIGNING.add(id);
  let dispatched = false;
  let pending = null;
  try {
    // Same fresh rebuild/refusal guard as confirmCombineBroadcast, with per-copy equality.
    const fresh = await buildPlanCombine(plan);
    if (CURRENT_PLAYER !== player || !chainCanCombine(chainPlans()[id])
        || review.witness !== JSON.stringify(chainSignWitness(chainPlans()[id])) || !combineReviewMatches(review.build.preview, fresh.preview) || planBuildFacts(fresh) !== planBuildFacts(review.build)) throw new Error(CHANGED_PLAN);
    const operation = fresh.operation;
    pending = { tx_id: null, nonce: JSON.parse(operation[1].json).n, signed_at: new Date().toISOString(), operation, recovery: fresh.recovery || null };
    // Persist before invoking the wallet: a reload must never offer the same cards twice.
    await withStagedAccountLock(player, locked => {
      const durable = JSON.parse(localStorage.getItem(`ceres:stage-plan:v1:${player}`) || 'null');
      const wanted = new Set(activeChainInputs(plan).map(input => input.uid));
      // A01: read ALL durable plans, including a different id using these cards.
      if (!durable || Object.values(durable.chains || {}).some(other => other.combine && !['done', 'cancelled'].includes(other.state)
          && other.inputs?.some(input => wanted.has(input.uid)))) throw new Error('A combine is already in progress or unresolved.');
      if (!locked) throw new Error('Safe combine locking is unavailable. These copies remain held.');
      const storedPlan = durable.chains?.[id];
      if (!storedPlan || !chainCanCombine(storedPlan) || review.witness !== JSON.stringify(chainSignWitness(storedPlan))) throw new Error(CHANGED_PLAN);
      saveChain(id, { ...plan, state: 'combine_signed', combine: pending, problem: null }, { locked: true });
    });
    const stored = JSON.parse(localStorage.getItem(`ceres:stage-plan:v1:${player || BOARD?.player}`) || 'null')?.chains?.[id];
    if (stored?.state !== 'combine_signed' || stored.combine?.nonce !== pending.nonce
        || chainPlans()[id]?.combine?.nonce !== pending.nonce) throw new Error('The pending combine could not be saved. Nothing was sent.');
    dispatched = true;
    const receipt = await beginSharedOperation({ account: fresh.preview.account, operations: [operation], family: 'combine', context: `Combine ${plan.card.name}`,
      onResponse: response => {
        const txId = response?.result?.id || response?.result?.tx_id || response?.tx_id;
        if (response?.success && txId) completeChainAttempt(player, id, pending.nonce, txId).catch(() => {});
      } });
    await completeChainAttempt(player, id, pending.nonce, receipt.txId);
    if (CURRENT_PLAYER !== player) return false;
    receipt.pollVerify = next => verifyPlanCombine(id, next);
    await verifyPlanCombine(id, receipt);
    return true;
  } catch (error) {
    if (pending && error.txId) await completeChainAttempt(player, id, pending.nonce, error.txId);
    if (CURRENT_PLAYER !== player) return false;
    if (dispatched && error.broadcasted === false && !error.ambiguousBroadcast && !error.inclusionUnobserved) saveChain(id, { ...plan, cleared_combine_nonce: pending.nonce });
    else if (dispatched) saveChain(id, { ...chainPlans()[id], combine: { ...chainPlans()[id].combine, tx_id: error.txId || chainPlans()[id].combine?.tx_id }, problem: { code: 'combine_uncertain' } });
    else saveChain(id, { ...chainPlans()[id], problem: { code: 'combine_refused', detail: error.message || CHANGED_PLAN } });
    return false;
  } finally { PLAN_SIGNING.delete(id); renderCheckoutSurface(); }
}

export function rebindPlanPlacement(id) {
  const plan = chainPlans()[id];
  if (!plan?.result || plan.problem) return;
  const ready = chainPlacementReady(plan, BOARD?.generated_at);
  for (const entry of CERES_STAGE.filter(item => item.chain_id === id && item.action !== 'unstake' && !item.txId)) {
    entry.asset_ref = plan.result.uid;
    entry._asset = { ...plan.card, ...plan.result, uid: plan.result.uid, asset_ref: plan.result.uid, chain_id: id,
      planned: false, land_base_pp: plan.result.base_pp };
    entry.combine_verified_at = plan.result.observed_at;
    entry.cooldown = { ready, state: ready ? 'ready' : 'planned', ready_at: null, reason: ready ? 'combine confirmed' : 'Waiting for the board read after the combine' };
    const row = BOARD?.plots?.find(item => item.deed_uid === entry.deed_uid);
    if (row) { applyStageToRow(row, { force: true }); updateMountedRow(row.deed_uid); }
    if (ready) refreshStageImpactForRow(entry.deed_uid);
  }
  persistStagePlans();
}

export async function verifyPlanCombine(id, receipt = null) {
  const plan = chainPlans()[id];
  const player = CURRENT_PLAYER;
  if (!(plan?.state === 'combine_signed' || plan?.problem?.code === 'combine_mismatch') || !plan.combine?.tx_id || !plan.combine?.operation || PLAN_VERIFYING.has(id)) return;
  PLAN_VERIFYING.add(id);
  try {
    const response = await fetch(playerQueryAppend('/api/op-verify'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tx_id: plan.combine.tx_id, operations: [plan.combine.operation], plan_combine: true, combine_recovery: plan.combine.recovery }) });
    if (!response.ok) throw new Error('Verification unavailable');
    const verdict = await response.json();
    if (CURRENT_PLAYER !== player || chainPlans()[id]?.combine?.tx_id !== plan.combine.tx_id) return;
    const actual = verdict.combine_result;
    if (actual && activeChainInputs(plan).some(input => input.uid === actual.uid)) {
      const result = { uid: actual.uid, bcx: chainNumber(actual.bcx), level: chainNumber(actual.level), base_pp: chainNumber(actual.land_base_pp ?? actual.base_pp),
        observed_at: verdict.combine_observed_at || new Date().toISOString(), survivor_differs: actual.uid !== plan.expected.survivor_uid };
      const problem = verdict.state === 'domain-confirmed' && chainResultMatches(plan, result) ? null : { code: 'combine_mismatch' };
      saveChain(id, { ...plan, state: 'combined', result, problem });
      rebindPlanPlacement(id);
      if (receipt) { stopOperationPoll(receipt.entryId); publishOperationReceipt({ ...receipt, phase: 'confirmed', result: verdict }); }
      // The server requested one quick capture. Adopt that capture without starting another.
      await settleCombineRefresh(verdict.combine_refresh, { player, after: () => { rebindPlanPlacement(id); renderCheckoutSurface(); } });
    } else if (verdict.state === 'game-refused') {
      saveChain(id, { ...plan, state: 'ready', combine: null, cleared_combine_nonce: plan.combine.nonce, problem: { code: 'combine_refused', detail: 'The game refused the combine. Review the plan before trying again.' } });
      if (receipt) { stopOperationPoll(receipt.entryId); publishOperationReceipt({ ...receipt, phase: 'failed', result: verdict }); }
    } else if (receipt) {
      const next = publishOperationReceipt({ ...receipt, phase: 'in_flight', attempt: (receipt.attempt || 0) + 1, lastState: verdict.state });
      scheduleOperationPoll(next, value => verifyPlanCombine(id, value));
    }
  } catch (_) {
    // Unknown is not a refusal. The persisted transaction stays locked; Check again is by ID.
  } finally { PLAN_VERIFYING.delete(id); renderCheckoutSurface(); }
}

export async function resolvePlanMismatch(id, useCard) {
  const plan = chainPlans()[id];
  if (plan?.problem?.code !== 'combine_mismatch') return;
  if (useCard && !chainCompleteResult(plan.result)) return;
  if (!useCard) {
    for (const entry of [...CERES_STAGE].filter(item => item.chain_id === id && item.action !== 'unstake')) await removeCheckoutStage(entry);
    if (CERES_STAGE.some(entry => entry.chain_id === id && entry.action !== 'unstake')) return;
  }
  saveChain(id, { ...plan, problem: null, mismatch_accepted: useCard });
  rebindPlanPlacement(id);
  renderCheckoutSurface();
}

window.addEventListener('ceres:chain-plans-restored', () => {
  if (!stagePlansReady()) return;
  for (const [id, plan] of activeChainPlans()) {
    if (plan.state === 'combine_signed') verifyPlanCombine(id);
    if (plan.state === 'combined') rebindPlanPlacement(id);
  }
});
