// B01/B02: decimal collection facts become numbers only in display models.
export function chainNumber(value) {
  if (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value) && value.length <= 64) value = Number(value);
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= Number.MAX_SAFE_INTEGER ? value : null;
}
export function migrateChainNumbers(plan) {
  const visit = (value, depth = 0) => {
    if (!value || typeof value !== 'object' || depth > 32) return;
    for (const [key, item] of Object.entries(value)) {
      if (key === 'recovery') continue; // authenticated bytes must never be rewritten
      if (['land_base_pp', 'base_pp'].includes(key) && typeof item === 'string') {
        const number = chainNumber(item);
        if (number !== null) value[key] = number;
      } else visit(item, depth + 1);
    }
  };
  visit(plan);
  return plan;
}
// Recovery has its own raw-fact schema; authentication remains server-only.
export function validChainRecovery(envelope, depth = 0) {
  const payload = envelope?.payload;
  if (!envelope || Object.keys(envelope).some(key => !['payload', 'seal', 'payload_json'].includes(key))) return false;
  if (!payload || !/^[a-f0-9]{64}$/.test(envelope.seal || '') || typeof payload.account !== 'string'
      || typeof payload.nonce !== 'string' || !Array.isArray(payload.records) || !payload.records.length
      || payload.records.length > 200 || !payload.expected || typeof payload.expected !== 'object' || Array.isArray(payload.expected)) return false;
  const safe = (value, depth = 0) => depth > 32 ? false : typeof value === 'number' ? chainNumber(value) !== null
    : typeof value === 'string' ? value.length <= 65536 && !/[<>]/.test(value)
    : Array.isArray(value) ? value.every(item => safe(item, depth + 1))
    : value && typeof value === 'object' ? Object.entries(value).every(([key, item]) =>
      (!['level', 'bcx', 'bcx_max', 'cc', 'base_pp', 'land_base_pp', 'card_detail_id', 'edition', 'slot_index'].includes(key) || item === null || chainNumber(item) !== null) && safe(item, depth + 1)) : true;
  if (envelope.payload_json !== undefined) {
    const same = (a, b) => a === b || (a && b && typeof a === 'object' && typeof b === 'object'
      && Array.isArray(a) === Array.isArray(b) && Object.keys(a).length === Object.keys(b).length
      && Object.keys(a).every(key => Object.prototype.hasOwnProperty.call(b, key) && same(a[key], b[key])));
    try {
      const encoded = JSON.parse(envelope.payload_json);
      if (!safe(encoded, depth + 1) || !same(encoded, payload)) return false;
    } catch (_) { return false; }
  }
  return payload.records.every(row => row && typeof row.uid === 'string' && row.uid && chainNumber(row.bcx) > 0) && safe(payload, depth + 1);
}
// A08: observed collection PP strings have three decimal places (audit2).
const CHAIN_PP_DECIMALS = 3;
export function chainPP(value) {
  const number = chainNumber(value);
  if (number === null) return null;
  // Decimal text + integer scaling: round half up without a binary multiplication.
  const [mantissa, exponent = '0'] = String(number).split('e');
  const [whole, fraction = ''] = mantissa.split('.');
  const digits = whole + fraction;
  const shift = CHAIN_PP_DECIMALS + Number(exponent) - fraction.length;
  if (shift >= 0) return (BigInt(digits) * 10n ** BigInt(shift)).toString();
  const divisor = 10n ** BigInt(-shift);
  return ((BigInt(digits) + divisor / 2n) / divisor).toString();
}

// A03: validate stored values before any renderer or readiness derivation.
export function validChainPlan(plan) {
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const numeric = new Set(['level', 'bcx', 'bcx_max', 'cc', 'base_pp', 'land_base_pp', 'card_detail_id', 'edition', 'slot_index']);
  const records = new Set(['card', 'expected', 'result', 'combine', 'unstake', 'origin', 'source', 'asset', 'problem', 'recovery', 'payload']);
  const text = new Set(['tx_id', 'nonce', 'uid', 'name', 'image', 'signed_at', 'confirmed_at', 'observed_at']);
  // a card snapshot may carry a negative ability value (Rationing Lite -0.03); only the named numeric fields must be
  // non-negative -- any other number only has to be finite, as on the server. Rejecting it held every saved change forever.
  const visit = (value, depth = 0) => depth > 32 ? false : Array.isArray(value) ? value.every(item => visit(item, depth + 1)) : !object(value) ? typeof value !== 'number' || (Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER) : Object.entries(value).every(([key, item]) =>
    (key === 'recovery' && item != null ? validChainRecovery(item, depth + 1) : (item == null || ((!numeric.has(key) || (typeof item === 'number' && Number.isFinite(item) && item >= 0 && item <= Number.MAX_SAFE_INTEGER))
      && (!records.has(key) || object(item)) && (!text.has(key) || typeof item === 'string'))) && visit(item, depth + 1)));
  if (object(plan) && plan.tombstone === true) return ['done', 'cancelled'].includes(plan.state) && Array.isArray(plan.inputs) && !plan.inputs.length;
  return object(plan) && plan.version === 2 && ['planned', 'cooling', 'ready', 'combine_signed', 'combined', 'done', 'cancelled'].includes(plan.state)
    && object(plan.card) && object(plan.expected) && object(plan.origin) && Array.isArray(plan.inputs)
    && plan.inputs.every(input => object(input) && typeof input.uid === 'string' && input.uid && typeof input.cc === 'number' && input.cc > 0
      && ['active', 'left_out', 'unavailable'].includes(input.status) && ['free', 'cooling', 'staked'].includes(input.kind))
    && new Set(plan.inputs.map(input => input.uid)).size === plan.inputs.length && visit(plan)
    // (audit #7): every problem code a consumer reads with string operations must be a string
    && [plan, ...plan.inputs].every(item => item.problem == null || item.problem.code == null || typeof item.problem.code === 'string')
    && (!plan.result || plan.problem?.code === 'combine_mismatch' || ['bcx', 'base_pp', 'level'].every(key => typeof plan.result[key] === 'number' && Number.isFinite(plan.result[key])))
    && (!plan.combine || (typeof plan.combine.nonce === 'string' && plan.combine.nonce && Array.isArray(plan.combine.operation)
      && plan.combine.operation[0] === 'custom_json' && object(plan.combine.operation[1])));
}

// pure plan model. End times are observed game verdicts, never signing time + 72h.
export function activeChainInputs(plan) {
  return (plan.inputs || []).filter((input) => input.status === 'active');
}

export function chainInputReady(input, now = Date.now()) {
  if (input.status !== 'active') return false;
  if (input.kind === 'free') return true;
  if (input.kind === 'staked' && !input.unstake?.confirmed_at) return false;
  return Number.isFinite(Date.parse(input.ready_at)) && Date.parse(input.ready_at) <= now;
}

export function deriveChain(plan, now = Date.now()) {
  if (['cancelled', 'done', 'combine_signed', 'combined'].includes(plan.state)) return { state: plan.state, ready_at: plan.ready_at, problem: plan.problem };
  const inputs = activeChainInputs(plan);
  const planned = inputs.some((input) => input.kind === 'staked' && !input.unstake?.tx_id);
  const timed = inputs.filter((input) => input.kind !== 'free');
  const known = timed.every((input) => Number.isFinite(Date.parse(input.ready_at)) && (input.kind !== 'staked' || input.unstake?.confirmed_at));
  const ready_at = known && timed.length ? new Date(Math.max(...timed.map((input) => Date.parse(input.ready_at)))).toISOString() : null;
  const refused = inputs.find((input) => input.problem?.code === 'unstake_refused');
  const problem = refused?.problem || (typeof plan.problem?.code === 'string' && plan.problem.code.startsWith('combine_') ? plan.problem : null) || (inputs.length < 2 ? { code: 'not_enough_cards' } :
    (plan.inputs || []).some((input) => input.status === 'unavailable') ? { code: 'copy_unavailable' } : null);
  return { state: planned ? 'planned' : inputs.length >= 2 && inputs.every((input) => chainInputReady(input, now)) ? 'ready' : 'cooling', ready_at, problem };
}

export function selectChainLevel(copies, anchorUid, threshold) {
  const anchor = copies.find((copy) => copy.uid === anchorUid && copy.eligible);
  if (!anchor) return [];
  const selected = [anchor.uid];
  let cc = Number(anchor.bcx);
  const rank = { free: 0, cooling: 1, staked: 2 };
  const ordered = copies.filter((copy) => copy.eligible && copy.uid !== anchorUid)
    .sort((a, b) => rank[a.kind] - rank[b.kind] || Number(b.bcx) - Number(a.bcx) || a.uid.localeCompare(b.uid));
  for (const copy of ordered) {
    if (cc >= threshold && selected.length >= 2) break;
    selected.push(copy.uid);
    cc += Number(copy.bcx);
  }
  return selected;
}

export function chainPlotLosses(copies) {
  const plots = new Map();
  copies.filter((copy) => copy.kind === 'staked' && copy.source).forEach((copy) => {
    const key = copy.source.deed_uid;
    if (!plots.has(key)) plots.set(key, []);
    plots.get(key).push(copy);
  });
  return [...plots.entries()].map(([deed_uid, inputs]) => ({ deed_uid, inputs }));
}

export function createChain({ id, origin, card, copies, expected }, now = new Date().toISOString()) {
  const plan = { version: 2, id, created_at: now, updated_at: now, state: 'planned', problem: null,
    origin, card: Object.fromEntries(['name', 'card_detail_id', 'edition', 'foil', 'gold', 'card_set', 'rarity', 'image', 'level', 'bcx', 'base_pp', 'land_base_pp'].filter((key) => card[key] !== undefined).map((key) => [key, card[key]])), inputs: copies.map((copy) => ({ uid: copy.uid, cc: copy.bcx, kind: copy.kind,
      source: copy.source || null, unstake: null, ready_at: copy.ready_at || null, status: 'active', left_out_choice: null,
      asset_ref: copy.asset_ref || copy.uid, asset: copy.asset ? { ...copy.asset } : null, updated_at: now })),
    expected, ready_at: null, combine: null, result: null };
  return migrateChainNumbers(Object.assign(plan, deriveChain(plan)));
}

export function reconcileChainCopies(plan, copies, observedAt, now = Date.now()) {
  const byUid = new Map(copies.map((copy) => [copy.uid, copy]));
  for (const input of activeChainInputs(plan)) {
    const copy = byUid.get(input.uid);
    if (input.kind === 'staked' && input.unstake?.signed_at && Date.parse(observedAt) <= Date.parse(input.unstake.signed_at)) continue;
    if (!copy || !copy.eligible || (copy.kind === 'staked' && (input.kind !== 'staked' || input.unstake?.confirmed_at || copy.source?.deed_uid !== input.source?.deed_uid))) {
      input.status = 'unavailable';
      input.updated_at = new Date(now).toISOString();
      continue;
    }
    if (input.kind === 'free' && copy.kind === 'cooling') input.kind = 'cooling';
    if (copy.kind !== 'staked') {
      // A free live verdict is evidence of readiness, including a cleared cooldown date.
      input.ready_at = copy.ready_at || (copy.kind === 'free' ? observedAt : null);
    }
  }
  // A09: observed facts are not a new user edit.
  Object.assign(plan, deriveChain(plan, now));
  return plan;
}

// placement identity belongs to the plan, never the predicted survivor.
export function chainPickerVisible(plan) {
  if (!plan || ['cancelled', 'done', 'combined', 'combine_signed'].includes(plan.state)) return false;
  const inputs = activeChainInputs(plan);
  return inputs.length >= 2 && (!inputs.some(input => input.kind === 'staked') || inputs.some(input => input.unstake?.tx_id));
}

export function chainReturn(plan, deedUid, slotIndex) {
  const sources = activeChainInputs(plan || {}).filter(input => input.kind === 'staked' && input.source?.deed_uid === deedUid)
    .map(input => input.source).sort((a, b) => a.slot_index - b.slot_index);
  const source = sources.find(source => source.slot_index === slotIndex) || sources[0];
  if (!source) return null;
  const sameSlot = source.slot_index === slotIndex;
  const slot = source.slot_index + 1;
  // say what happens -- the upgraded card returns.
  return { sameSlot, slot, label: sameSlot ? `Returns upgraded to slot ${slot}` : `Returns upgraded to this plot (was slot ${slot})`,
    picker: sameSlot ? 'Returns upgraded to this slot' : `Returns upgraded to this plot (was slot ${slot})`,
    short: sameSlot ? `To slot ${slot}` : 'To this plot' }; // the box under a tile, first of its two rows
}

// the slot the plan stages the upgraded card back into (the clicked worker's own slot), while that is still owed.
export function chainStageBackSource(plan) {
  if (!plan?.origin?.stage_back || plan.origin.stage_back_at || !['planned', 'cooling', 'ready'].includes(plan.state)) return null;
  const anchor = activeChainInputs(plan).find(input => input.uid === plan.origin.asset_uid && input.kind === 'staked' && input.source);
  return anchor ? anchor.source : null;
}

export function chainPlacementBlocked(plan, deedUid, slotIndex) {
  const input = activeChainInputs(plan || {}).find(input => input.kind === 'staked' && input.source?.deed_uid === deedUid && input.source?.slot_index === slotIndex);
  if (!input || input.unstake?.confirmed_at) return '';
  return input.unstake?.tx_id ? "Wait a moment: this slot's unstake is still being confirmed." : "Sign this slot's unstake first; then the new card can go here.";
}

// the reviewed witness excludes display state, never selected inputs.
export function chainSignWitness(plan) {
  return { state: deriveChain(plan).state, card: plan.card,
    inputs: activeChainInputs(plan).map(input => ({ uid: input.uid, cc: input.cc })), expected: plan.expected };
}

export function chainCanCombine(plan) {
  const derived = deriveChain(plan);
  return plan.state === 'ready' && derived.state === 'ready' && !derived.problem && !plan.combine && activeChainInputs(plan).length >= 2
    && ['bcx', 'base_pp', 'level'].every(key => typeof plan.expected?.[key] === 'number' && Number.isFinite(plan.expected[key]));
}

export function chainPlacementReady(plan, captureAt) {
  return plan?.state === 'combined' && !plan.problem && !!plan.result?.uid
    && Date.parse(captureAt) > Date.parse(plan.result.observed_at);
}

export function chainCompleteResult(result) {
  return ['bcx', 'base_pp', 'level'].every(key => typeof result?.[key] === 'number' && chainNumber(result[key]) !== null)
    && result.bcx > 0 && result.level > 0;
}
export function chainResultMatches(plan, result) {
  // A05: incomplete results must never release placement.
  return chainCompleteResult(result) && result.bcx === plan.expected?.bcx
    && chainPP(result.base_pp) === chainPP(plan.expected?.base_pp);
}
