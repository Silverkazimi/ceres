// GENERATED from ceres/resources.py by `python3 -m ceres.resources --write` -- do not hand-edit.
// The one resource registry for the browser; tests/ceres/test_resource_registry.py checks this file
// byte-for-byte against the Python table, so server and browser lists can never drift.
export const CERES_RESOURCES = Object.freeze([
  Object.freeze({ symbol: 'GRAIN', kind: 'natural', label: 'Grain', icon: 'resources/grain.png', chain_id: 1, worksite_key: 'grain', worksite_name: 'Grain Farm', rate_per_pp: 0.02, harvestable: true, regional: true, poolable: true, transferable: true, soulbound: false, cost_input: true, material_input: false }),
  Object.freeze({ symbol: 'WOOD', kind: 'natural', label: 'Wood', icon: 'resources/wood_200.webp', chain_id: 5, worksite_key: 'wood', worksite_name: 'Logging Camp', rate_per_pp: 0.005, harvestable: true, regional: true, poolable: true, transferable: true, soulbound: false, cost_input: true, material_input: true }),
  Object.freeze({ symbol: 'STONE', kind: 'natural', label: 'Stone', icon: 'resources/stone_200.webp', chain_id: 6, worksite_key: 'stone', worksite_name: 'Quarry', rate_per_pp: 0.002, harvestable: true, regional: true, poolable: true, transferable: true, soulbound: false, cost_input: true, material_input: true }),
  Object.freeze({ symbol: 'IRON', kind: 'natural', label: 'Iron', icon: 'resources/iron_200.webp', chain_id: 7, worksite_key: 'iron', worksite_name: 'Ore Mine', rate_per_pp: 0.0005, harvestable: true, regional: true, poolable: true, transferable: true, soulbound: false, cost_input: true, material_input: true }),
  Object.freeze({ symbol: 'VOUCHER', kind: 'account', label: 'Voucher', icon: 'resources/voucher.svg', chain_id: null, worksite_key: null, worksite_name: null, rate_per_pp: null, harvestable: false, regional: false, poolable: true, transferable: false, soulbound: false, cost_input: false, material_input: false }),
  Object.freeze({ symbol: 'SPS', kind: 'account', label: 'SPS', icon: 'resources/sps.png', chain_id: 2, worksite_key: 'sps', worksite_name: 'Shard Mine', rate_per_pp: null, harvestable: true, regional: false, poolable: true, transferable: false, soulbound: false, cost_input: false, material_input: false }),
  Object.freeze({ symbol: 'RESEARCH', kind: 'special', label: 'Research', icon: 'resources/research.png', chain_id: 3, worksite_key: 'research', worksite_name: 'Research Hut', rate_per_pp: 0.0001, harvestable: true, regional: true, poolable: false, transferable: false, soulbound: true, cost_input: false, material_input: false }),
  Object.freeze({ symbol: 'AURA', kind: 'special', label: 'Aura', icon: 'resources/aura_200.webp', chain_id: 38, worksite_key: 'aura', worksite_name: 'Aura Lab', rate_per_pp: 0.0005, harvestable: true, regional: true, poolable: false, transferable: false, soulbound: true, cost_input: false, material_input: false }),
  Object.freeze({ symbol: 'DEC', kind: 'account', label: 'DEC', icon: 'land/token-dec.png', chain_id: null, worksite_key: null, worksite_name: null, rate_per_pp: null, harvestable: false, regional: false, poolable: false, transferable: false, soulbound: false, cost_input: false, material_input: false }),
]);
const CERES_RESOURCE_ORDERS = Object.freeze({
  wallet_tabs: Object.freeze(['DEC', 'SPS', 'VOUCHER', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA']),
  harvest: Object.freeze(['GRAIN', 'WOOD', 'STONE', 'IRON', 'AURA', 'SPS', 'RESEARCH']),
});
// A fresh array each call, so a caller may treat its list as its own (as the old literals were).
export function resourceOrder(name) { return [...(CERES_RESOURCE_ORDERS[name] || [])]; }
// Symbols in canonical order whose every named flag is true.
export function resourceSymbolsWhere(...flags) { return CERES_RESOURCES.filter(r => flags.every(f => r[f])).map(r => r.symbol); }
// {lower-case symbol: value of `fieldName`} for the entries where it is set.
export function resourceFieldMap(fieldName) { return Object.fromEntries(CERES_RESOURCES.filter(r => r[fieldName] != null).map(r => [r.symbol.toLowerCase(), r[fieldName]])); }
