export const HARVEST_COST_ORDER = ['GRAIN', 'WOOD', 'STONE', 'IRON', 'AURA', 'SPS', 'RESEARCH'];

export function netAfterCosts(projection) {
  const symbol = String(projection?.token || '').toUpperCase();
  if (!symbol) return [];
  const costs = projection?.costs || {};
  const received = projection?.net;
  const ownCost = costs[symbol];
  const known = received != null && Number.isFinite(Number(received))
    && (ownCost == null ? !Object.hasOwn(costs, symbol) : Number.isFinite(Number(ownCost)));
  const gain = { symbol, amount: known ? Number(received) - Number(ownCost || 0) : null, kind: 'gain' };
  const rank = key => HARVEST_COST_ORDER.includes(key) ? HARVEST_COST_ORDER.indexOf(key) : HARVEST_COST_ORDER.length;
  const debits = Object.keys(costs).filter(key => key !== symbol && (costs[key] == null || !Number.isFinite(Number(costs[key])) || Number(costs[key]) > 0))
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    .map(key => ({ symbol: key, amount: costs[key] == null || !Number.isFinite(Number(costs[key])) ? null : -Number(costs[key]), kind: 'cost' }));
  return [gain, ...debits];
}
