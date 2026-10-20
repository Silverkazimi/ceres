import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

globalThis.window = new EventTarget();
globalThis.CustomEvent = class extends Event { constructor(type, options = {}) { super(type); this.detail = options.detail; } };
const { balancesChanged, onBalancesChanged } = await import('../../../ceres/static/balances-bus.js');
const source = name => fs.readFileSync(new URL(`../../../ceres/static/${name}`, import.meta.url), 'utf8');
const listener = (name, scope) => {
  const match = source(name).match(/onBalancesChanged\(([\s\S]*?), \{ player(?:\s*\}|:)/);
  assert.ok(match, `${name}: listener missing`);
  return vm.runInNewContext(`(${match[1]})`, scope);
};
const sleep = () => new Promise(resolve => setTimeout(resolve, 30));
let serial = 0;
async function verify(name, scope, count, setBusy, assets, readCount = 1) {
  const player = `consumer_${++serial}`;
  const stop = onBalancesChanged(listener(name, scope), { player, delay: 5 });
  balancesChanged({ player: 'another_player', assets, id: `${name}-foreign` });
  await sleep(); assert.equal(count(), 0, `${name}: foreign player`);
  setBusy(true);
  balancesChanged({ player, assets, id: `${name}-busy` });
  await sleep(); assert.equal(count(), 0, `${name}: busy run`);
  setBusy(false);
  balancesChanged({ player, assets, id: `${name}-one` });
  balancesChanged({ player, assets, id: `${name}-two` });
  await sleep(); assert.equal(count(), readCount, `${name}: one debounced reread`);
  stop();
}
{
  let reads = 0; const co = { stage: 'review' };
  await verify('card-checkout.js', { co, requoteAfterWallet: () => { reads++; } }, () => reads,
    busy => { co.stage = busy ? 'outcome' : 'review'; }, ['DEC']);
}
{
  let stateReads = 0, previewReads = 0; const M = { open: true, run: null };
  await verify('harvest-modal.js', { M, loadState: () => { stateReads++; }, loadPreview: () => { previewReads++; } },
    () => stateReads, busy => { M.run = busy ? { busy: true } : null; }, ['GRAIN']);
  assert.equal(previewReads, 1, 'harvest preview redraw');
}
{
  let reads = 0, renders = 0; const S = { busy: false, chainActive: false };
  await verify('harvest-routing.js', { S, isCurrent: () => true, api: async () => { reads++; return { amount: 42 }; }, onRender: () => { renders++; } },
    () => reads, busy => { S.busy = busy; }, ['DEC']);
  assert.equal(S.data.amount, 42);
  assert.equal(renders, 1);
}
{
  let reads = 0, boardLoadController = null; const BOARD_GENERATION = 1, CURRENT_PLAYER = 'consumer_4';
  await verify('board.js', { get boardLoadController() { return boardLoadController; }, BOARD_GENERATION, CURRENT_PLAYER,
    refreshBoardTotals: () => { reads++; } }, () => reads,
    busy => { boardLoadController = busy ? {} : null; }, ['DEC']);
}
console.log('public consumer event, debounce, busy and player checks passed');
