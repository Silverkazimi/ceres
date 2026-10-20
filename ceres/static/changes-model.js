// phase 2 ("State, colours and counting")
// the one projection behind the Changes button and panel -- every staged intention once, grouped by its destination plot
// with its display status. Pure: no fetch, no storage, no DOM. Signing still goes only through checkoutSignableEntries().

// Display precedence: in flight, refused, orange (staked elsewhere), waiting (cooldown / Survival), included now.
export function changeStatus(entry, aside) {
  if (entry.txId && (entry.broadcast_at || entry.queued_at)) return 'working';
  if (aside === 'invalid') return 'invalid';
  if (aside === 'refused') return 'refused';
  if (aside === 'elsewhere') return 'orange';
  if (aside === 'blocked' || aside === 'power') return 'waiting';
  return 'included';
}

function parentKey(synthetic) {
  const r = synthetic._restake;
  return r ? `${r.target_deed_uid}|${r.group_id}|${r.slot_index}` : null;
}

// A synthetic source unstake is not another intention: it shows as its parent's step 1. Only when the parent is gone
// (e.g. restored from the in-flight store alone) is it listed by itself.
export function buildChangesModel(stage, setAside) {
  const parents = new Set(stage.filter((entry) => !entry._synthetic).map((entry) => `${entry.deed_uid}|${entry.group_id}|${entry.slot_index}`));
  const intents = stage.filter((entry) => !entry._synthetic || !parents.has(parentKey(entry)));
  const counts = { included: 0, orange: 0, waiting: 0, invalid: 0, refused: 0, working: 0 };
  const groups = new Map();
  intents.forEach((entry) => {
    const status = changeStatus(entry, entry._synthetic ? null : setAside(entry));
    counts[status] += 1;
    if (!groups.has(entry.deed_uid)) groups.set(entry.deed_uid, []);
    groups.get(entry.deed_uid).push({ entry, status });
  });
  return { intents, counts, groups };
}

// Filters only change what is shown, never what is signed.
export function matchesFilter(status, filter) {
  if (filter === 'included') return status === 'included' || status === 'orange' || status === 'working';
  if (filter === 'waiting') return status === 'waiting' || status === 'orange';
  if (filter === 'attention') return status === 'refused' || status === 'invalid';
  return true;
}
