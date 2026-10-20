import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.window = new EventTarget();
globalThis.CustomEvent = class extends Event { constructor(type, options = {}) { super(type); this.detail = options.detail; } };
const { balancesChanged, onBalancesChanged } = await import('../../../ceres/static/balances-bus.js');
const amounts = { DEC: 100, SPS: 10, GRAIN: 30, CARDS: 2 };
const shown = { wallet: { ...amounts }, checkout: 100, plaque: 30, header: 2 };
let walletReads = 0, checkoutReads = 0, plaqueReads = 0, headerReads = 0;
onBalancesChanged(({ assets }) => {
  if (!assets.some(a => ['DEC', 'SPS', 'GRAIN'].includes(a))) return;
  walletReads++; shown.wallet = { ...amounts };
}, { player: 'player_owner', delay: 5 });
onBalancesChanged(({ assets }) => {
  if (!assets.includes('DEC')) return;
  checkoutReads++; shown.checkout = amounts.DEC;
}, { player: 'player_owner', delay: 5 });
onBalancesChanged(({ assets }) => {
  if (!assets.includes('GRAIN')) return;
  plaqueReads++; shown.plaque = amounts.GRAIN;
}, { player: 'player_owner', delay: 5 });
onBalancesChanged(({ assets }) => {
  if (!assets.includes('CARDS')) return;
  headerReads++; shown.header = amounts.CARDS;
}, { player: 'player_owner', delay: 5 });
const sleep = () => new Promise(resolve => setTimeout(resolve, 15));
const cases = [
  ['claim', 'SPS', 3], ['regional DEC stake', 'DEC', -2], ['regional DEC unstake', 'DEC', 2],
  ['swap', 'GRAIN', -3], ['transfer', 'GRAIN', -2], ['LP add', 'DEC', -2], ['LP withdraw', 'DEC', 2],
  ['harvest', 'GRAIN', 2], ['harvest-all', 'GRAIN', 2], ['harvest routing', 'GRAIN', -1],
  ['market purchase', 'DEC', -4], ['Buy & max', 'CARDS', 1], ['combine', 'CARDS', -1],
  ['land stake', 'CARDS', -1], ['land unstake', 'CARDS', 1],
];
const lines = [];
for (const [type, asset, delta] of cases) {
  const before = structuredClone(shown);
  const reads = [walletReads, checkoutReads, plaqueReads, headerReads];
  // The mocked game first reports pending: no notification or read is permitted.
  const game = { status: 'pending' };
  if (game.status === 'confirmed') balancesChanged({ player: 'player_owner', assets: [asset], id: type });
  await sleep();
  assert.deepEqual([walletReads, checkoutReads, plaqueReads, headerReads], reads);
  game.status = 'confirmed'; amounts[asset] += delta;
  balancesChanged({ player: 'player_owner', assets: [asset], regions: ['region-1'], id: type });
  balancesChanged({ player: 'player_owner', assets: [asset], regions: ['region-1'], id: type });
  await sleep();
  assert.equal(shown[asset === 'DEC' ? 'checkout' : asset === 'GRAIN' ? 'plaque' : asset === 'CARDS' ? 'header' : 'wallet'][asset === 'SPS' ? asset : undefined] ?? shown[asset === 'DEC' ? 'checkout' : asset === 'GRAIN' ? 'plaque' : 'header'], amounts[asset]);
  lines.push(JSON.stringify({ transaction: type, before, after: structuredClone(shown), reads: [walletReads - reads[0], checkoutReads - reads[1], plaqueReads - reads[2], headerReads - reads[3]] }));
}
const burstReads = [walletReads, checkoutReads, plaqueReads, headerReads];
amounts.DEC += 1; amounts.GRAIN += 1;
balancesChanged({ player: 'player_owner', assets: ['DEC'], id: 'burst-dec' });
balancesChanged({ player: 'player_owner', assets: ['GRAIN'], id: 'burst-grain' });
await sleep();
assert.deepEqual([walletReads - burstReads[0], checkoutReads - burstReads[1], plaqueReads - burstReads[2], headerReads - burstReads[3]], [1, 1, 1, 0]);
const noChangeReads = [walletReads, checkoutReads, plaqueReads, headerReads];
assert.equal(balancesChanged({ player: 'player_owner', assets: [], regions: [] }), false);
balancesChanged({ player: 'someone_else', assets: ['DEC'], id: 'other' });
await sleep();
assert.deepEqual([walletReads, checkoutReads, plaqueReads, headerReads], noChangeReads);
fs.writeFileSync(process.argv[2], lines.join('\n') + '\n');
