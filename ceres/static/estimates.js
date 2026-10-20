const ESTIMATE_STATUSES = new Set(['point', 'range', 'unknown', 'not_applicable']);

// Server estimates stay opaque here: normalization and aggregation never re-derive PP.
export function normalizeEstimate(raw) {
  if (!raw || typeof raw !== 'object' || !ESTIMATE_STATUSES.has(raw.status)) {
    return { status: 'point', value: 0, low: 0, high: 0, reason: null };
  }
  const asNum = (v) => (v === null || v === undefined ? null : Number(v));
  return { status: raw.status, value: asNum(raw.value), low: asNum(raw.low), high: asNum(raw.high), reason: raw.reason || null };
}

export function isValidEstimate(raw) {
  if (!raw || typeof raw !== 'object' || !ESTIMATE_STATUSES.has(raw.status)) return false;
  const numbers = [raw.value, raw.low, raw.high];
  return numbers.every((n) => n === null || (typeof n === 'number' && Number.isFinite(n)));
}

export function combineEstimates(estimates) {
  // Item-only moves are n/a, while one unknown component makes the aggregate unknown.
  const relevant = estimates.filter((e) => e.status !== 'not_applicable');
  if (!relevant.length) return { status: 'not_applicable', value: null, low: null, high: null, reason: null };
  if (relevant.some((e) => e.status === 'unknown')) return { status: 'unknown', value: null, low: null, high: null, reason: null };
  let value = 0, low = 0, high = 0, isRange = false;
  relevant.forEach((e) => {
    if (e.status === 'range') { isRange = true; low += e.low; high += e.high; }
    else { value += e.value; low += e.low; high += e.high; }
  });
  return isRange ? { status: 'range', value: null, low, high, reason: null } : { status: 'point', value, low, high, reason: null };
}
