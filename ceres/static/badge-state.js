import { combineEstimates, isValidEstimate } from './estimates.js';
import { isRuniWorker } from './format.js';

function stagedBadgeWorkers(row, staged) {
  let workers = Array.isArray(row?.workers) ? row.workers.slice() : null;
  if (!workers) return null;
  for (const entry of Array.isArray(staged) ? staged : []) {
    // Target rows are already mutated by staging.js; source rows need the moving card removed here.
    if (entry?._source_deed_uid !== row?.deed_uid || entry?.deed_uid === row?.deed_uid) continue;
    workers = workers.filter((worker) => String(worker?.asset_ref) !== String(entry?.asset_ref));
  }
  return workers;
}

export function effectiveUnderfilledState(row, staged = []) {
  const capacity = row?.capacity || {};
  const fallbackOpen = Number(capacity.open_slots);
  const fallbackWorkers = Number(capacity.worker_count);
  const effectiveWorkers = stagedBadgeWorkers(row, staged);
  // Lean rows deliberately omit workers even when assets_loaded is true, so only rows.js's detail marker permits derivation.
  if (row?._badge_roster_loaded !== true || !effectiveWorkers) {
    const openSlots = Number.isFinite(fallbackOpen) ? Math.max(0, fallbackOpen) : 0;
    return { active: Number.isFinite(fallbackWorkers) && fallbackWorkers > 0 && openSlots > 0, openSlots };
  }
  const stagedWorkers = effectiveWorkers.filter((worker) => Number(worker?.slot) >= 0);
  const monsterWorkers = stagedWorkers.filter((worker) => Number(worker?.slot) > 0).length;
  const cardCapacity = Number(capacity.card_capacity);
  const openSlots = Number.isFinite(cardCapacity)
    ? Math.max(0, cardCapacity - monsterWorkers)
    : Number.isFinite(fallbackOpen) ? Math.max(0, fallbackOpen) : 0;
  return { active: stagedWorkers.length > 0 && openSlots > 0, openSlots };
}

export function stagedBasePpCap(row) {
  const cap = Number(row?.base_pp_cap);
  // Empty/uncleared live snapshots report zero; match the candidate planner's prospective cap.
  return Number.isFinite(cap) && cap > 0 ? cap : 100000;
}

export function stageTotalBasePp(row, groups, staged) {
  // the figures come from the TILES -- the Base PP of the worker cards on the plot after the staged changes
  // never from estimates saved with each move (a plan kept before v0.9.318 has none, and read 0). The saved
  // estimates remain the fallback when a card's Base PP is unknown.
  const workerSlots = groups?.find?.((group) => group?.id === 'worker')?.slots || [];
  const occupants = workerSlots.filter(Boolean);
  const bases = occupants.map((occupant) => Number(occupant?.base_pp));
  const fromTiles = occupants.length === 0 || bases.every(Number.isFinite);
  const rawNow = Number(row?.base_pp); // the plot's raw Base PP, Runi included
  const cap = staged?.length ? stagedBasePpCap(row) : Number(row?.base_pp_cap);
  const runiAfter = Number(groups?.find?.((group) => group?.id === 'runi')?.slots?.[0]?.base_pp ?? 0);
  const savedEstimate = combineEstimates((Array.isArray(staged) ? staged : []).map((entry) => entry?.base).filter(isValidEstimate));
  const estimate = fromTiles && Number.isFinite(rawNow)
    ? { status: 'point', value: bases.reduce((sum, base) => sum + base, 0) + runiAfter - rawNow, low: null, high: null, reason: null }
    : savedEstimate;
  const effective = (raw) => Number.isFinite(cap) ? Math.min(raw - runiAfter, cap) + runiAfter : raw;
  const rawAfter = estimate?.status === 'point'
    ? rawNow + Number(estimate?.value || 0)
    : estimate?.status === 'range'
      ? { low: rawNow + Number(estimate?.low || 0), high: rawNow + Number(estimate?.high || 0) }
      : rawNow;
  const effectiveAfter = estimate?.status === 'range'
    ? { low: effective(rawAfter.low), high: effective(rawAfter.high) }
    : effective(rawAfter);
  const effectiveNow = Number.isFinite(Number(row?.base_pp_after_cap))
    ? Number(row?.base_pp_after_cap) : effective(rawNow);
  const lossRaw = estimate?.status === 'range' ? rawAfter.high : rawAfter;
  const loss = Number.isFinite(cap) ? Math.max(0, (lossRaw - runiAfter) - cap) : 0;
  return { estimate, rawAfter, effectiveAfter, effectiveNow, loss, cap, runiAfter };
}

export function stagedBasePpGroups(row, staged) {
  const workers = stagedBadgeWorkers(row, staged) || [];
  return [
    { id: 'worker', slots: workers.filter((worker) => Number(worker?.slot) > 0) },
    { id: 'runi', slots: [workers.find((worker) => isRuniWorker(worker)) || null] },
  ];
}

export function effectiveOverCapState(row, staged = []) {
  const rosterKnown = row?._badge_roster_loaded === true && Array.isArray(row?.workers);
  if (staged.length && rosterKnown) {
    const projected = stageTotalBasePp(row, stagedBasePpGroups(row, staged), staged);
    if (projected.estimate?.status === 'point' && Number.isFinite(projected.rawAfter)
        && Number.isFinite(projected.effectiveAfter) && Number.isFinite(projected.cap)) {
      return {
        active: projected.loss > 0,
        before: projected.rawAfter,
        after: projected.effectiveAfter,
        cap: projected.cap,
      };
    }
  }
  return {
    active: (row?.badges || []).includes('over_cap'),
    before: row?.base_pp,
    after: row?.base_pp_after_cap,
    cap: row?.base_pp_cap,
  };
}
