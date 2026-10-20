// the routing setup lists routes in the bundles ("approvals") Sign will use, from its very first paint. This is a port of
// the server compiler's grouping (ceres/routing_plan.py: _merge_sales, the wave split and _pack) that needs no amounts
// pools or network. tests/ceres/test_routing_layout_parity.py compares it with compile_manifest on random rule sets, so
// a change to either side fails there instead of making the screen jump when Sign is pressed.

const CAP = 4; // fides_op.DEFAULT_MAX_OPS_PER_TX
const NODE_LIMIT = 200000; // routing_plan._pack

const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

function packOperations(operations) {
  if (!operations.length) return [];
  const counts = new Map();
  for (const op of operations) counts.set(op.pool, (counts.get(op.pool) || 0) + 1);
  const ordered = [...operations].sort((a, b) => (counts.get(b.pool) - counts.get(a.pool)) || compare(a.pool, b.pool) || compare(a.id, b.id));
  const minimum = Math.max(Math.ceil(ordered.length / CAP), Math.max(...counts.values()));
  if (ordered.length > 400) { // one first-fit pass for a big plan (same bins as routing_plan._pack)
    const firstFit = [];
    for (const operation of ordered) {
      const bucket = firstFit.find(candidate => candidate.length < CAP && candidate.every(item => item.pool !== operation.pool));
      if (bucket) bucket.push(operation); else firstFit.push([operation]);
    }
    return firstFit.map(bucket => [...bucket].sort((a, b) => compare(a.id, b.id)));
  }
  for (let count = minimum; count <= ordered.length; count += 1) {
    let bins = Array.from({ length: count }, () => []);
    // first-fit without recursion first (identical to the first branch of the search below, and safe for any plan size).
    let fits = true;
    for (const operation of ordered) {
      const bucket = bins.find(candidate => candidate.length < CAP && candidate.every(item => item.pool !== operation.pool));
      if (!bucket) { fits = false; break; }
      bucket.push(operation);
    }
    if (fits) return bins.filter(bucket => bucket.length).map(bucket => [...bucket].sort((a, b) => compare(a.id, b.id)));
    bins = Array.from({ length: count }, () => []);
    let nodes = 0;
    const place = (index) => {
      nodes += 1;
      if (nodes > NODE_LIMIT) return false;
      if (index === ordered.length) return true;
      const seen = new Set();
      for (const bucket of bins) {
        const signature = bucket.map(item => item.pool).join('\u0000') + `\u0001${bucket.length}`;
        if (seen.has(signature)) continue;
        seen.add(signature);
        if (bucket.length === CAP || bucket.some(item => item.pool === ordered[index].pool)) continue;
        bucket.push(ordered[index]);
        if (place(index + 1)) return true;
        bucket.pop();
      }
      return false;
    };
    if (place(0)) return bins.filter(bucket => bucket.length).map(bucket => [...bucket].sort((a, b) => compare(a.id, b.id)));
  }
  return ordered.map(op => [op]);
}

// rules: the setup's saved rules. produced: Set of "region|SYMBOL" that harvested something (null: assume all did).
// Returns { bundles: [{ approval, lp, operations: [{ id, pool, routeIds }] }], unplaced: [rule ids] }.
export function plannedBundles(rules, produced = null) {
  const sells = new Map();   // merge key -> { ids: [...], routeIds: [...] }
  const liquidity = [];
  const unplaced = [];
  for (const rule of rules) {
    const symbol = String(rule.symbol || '');
    if (produced && !produced.has(`${rule.source_region}|${symbol.toUpperCase()}`)) { unplaced.push(rule.id); continue; }
    const slippage = String(rule.slippage ?? '2.5');
    const sellKey = `${rule.source_region}\u0000${symbol}\u0000${slippage}`;
    const addSale = (id) => {
      const group = sells.get(sellKey) || { pool: symbol, ids: [], routeIds: [] };
      group.ids.push(id);
      if (!group.routeIds.includes(rule.id)) group.routeIds.push(rule.id);
      sells.set(sellKey, group);
    };
    if (rule.kind === 'liquidity') {
      if (rule.dec_source === 'wallet') liquidity.push({ id: rule.id, pool: symbol, routeIds: [rule.id] });
      else { addSale(`${rule.id}:dec`); liquidity.push({ id: `${rule.id}:liquidity`, pool: symbol, routeIds: [rule.id] }); }
    } else addSale(rule.id);
  }
  const first = [...sells.values()].map(group => ({ id: [...group.ids].sort(compare).join('+'), pool: group.pool, routeIds: group.routeIds }));
  const bundles = [];
  for (const [wave, operations] of [[0, first], [1, liquidity]]) {
    for (const group of packOperations(operations)) bundles.push({ approval: bundles.length + 1, lp: wave === 1, operations: group });
  }
  return { bundles, unplaced };
}
