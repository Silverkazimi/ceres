// readiness is projected from the board and stage models already in memory.
let READY_TIMER = null;

function parsed(value) {
  const ms = Date.parse(value || '');
  return Number.isFinite(ms) ? ms : null;
}

export function collectReadyItems({ stage = [], constructions = [], plans = [], plotLabels = new Map(), now = Date.now() } = {}) {
  const items = [];
  const future = [];
  for (const entry of stage) {
    if (entry._asset?.planned || (entry.chain_id && (plans.find(plan => plan.id === entry.chain_id)?.state !== 'combined' || plans.find(plan => plan.id === entry.chain_id)?.problem || entry.cooldown?.ready !== true))) continue;
    if (entry.action === 'unstake' || !entry.cooldown || entry.txId) continue;
    const end = parsed(entry.cooldown.ready_at || entry.cooldown.readyAt || entry.cooldown.end_at);
    const key = `stake:${entry.deed_uid}:${entry.group_id}:${entry.slot_index}:${entry.asset_ref}`;
    if (entry.cooldown.ready === true || (entry.cooldown.state === 'ready' && entry.cooldown.ready !== false)) {
      items.push({ key, kind: 'stake', entry, label: `Ready to sign · ${entry._asset_name || entry.asset?.name || 'Card'} on ${plotLabels.get(entry.deed_uid) || 'plot'}, worker slot ${Number(entry.slot_index) + 1} (cooldown ended ${entry.cooldown.ready_at || entry.cooldown.survival_date || 'now'})` });
    } else if (end !== null && end <= now) {
      entry.cooldown = { ...entry.cooldown, ready: true, state: 'ready', reason: 'cooldown ended; the game checks it when you sign' };
      items.push({ key, kind: 'stake', entry, label: `Ready to sign · ${entry._asset_name || entry.asset?.name || 'Card'} on ${plotLabels.get(entry.deed_uid) || 'plot'}, worker slot ${Number(entry.slot_index) + 1} (cooldown ended ${entry.cooldown.ready_at || entry.cooldown.survival_date || 'now'})` });
    } else if (end !== null) future.push({ key, at: end, entry, kind: 'stake' });
  }
  for (const item of constructions) {
    const end = parsed(item.readyAt);
    if (item.kind === 'feed' || (item.kind === 'building' && end !== null && end <= now)) {
      const label = item.toLabel || item.row?.pending_change?.to_label || item.row?.worksite_completion?.to_label || 'the worksite';
      items.push({ key: `worksite:${item.deedUid}`, kind: 'worksite', label: `Ready to sign · Feed the workers · ${label} on ${plotLabels.get(item.deedUid) || 'plot'} is finished` });
    } else if (end !== null && end > now) future.push({ key: `worksite:${item.deedUid}`, at: end, item, kind: 'worksite' });
  }
  for (const plan of plans) {
    if (plan.state === 'ready' && !plan.problem && !plan.combine) items.push({ key: `plan:${plan.id}`, kind: 'plan', plan, label: `Ready to combine · ${plan.card?.name || 'card'}` });
    else if (plan.state === 'cooling' && !plan.problem && !plan.combine) {
      const end = parsed(plan.ready_at);
      if (end !== null && end <= now) {
        plan.state = 'ready';
        plan.inputs?.forEach((input) => {
          if (input.status === 'active' && input.kind !== 'free' && parsed(input.ready_at) !== null && parsed(input.ready_at) <= now) {
            input.cooldown = { ready: true, state: 'ready', reason: 'cooldown ended; the game checks it when you sign' };
          }
        });
        items.push({ key: `plan:${plan.id}`, kind: 'plan', plan, label: `Ready to combine · ${plan.card?.name || 'card'}` });
      } else if (end !== null && end > now) future.push({ key: `plan:${plan.id}`, at: end, plan, kind: 'plan' });
    }
  }
  // released plan placements join the ordinary ready stake rows above.
  return { items, future };
}

// Exactly one timeout exists; each surface render replaces it from the current models.
export function armReadyTimer(future = [], onExpire = () => {}, now = Date.now()) {
  if (READY_TIMER !== null) clearTimeout(READY_TIMER);
  READY_TIMER = null;
  const next = future.reduce((best, item) => item.at < best ? item.at : best, Infinity);
  if (!Number.isFinite(next)) return null;
  READY_TIMER = setTimeout(() => {
    READY_TIMER = null;
    for (const item of future.filter((entry) => entry.at <= Date.now())) {
      if (item.kind === 'stake') item.entry.cooldown = { ...item.entry.cooldown, ready: true, state: 'ready', reason: 'cooldown ended; the game checks it when you sign' };
      if (item.kind === 'worksite' && item.item) item.item.kind = 'feed';
      if (item.kind === 'plan' && item.plan) item.plan.state = 'ready';
    }
    onExpire();
  }, Math.min(0x7fffffff, Math.max(0, next - now)));
  return READY_TIMER;
}

export function resetReadyTimerForTests() { if (READY_TIMER !== null) clearTimeout(READY_TIMER); READY_TIMER = null; }
