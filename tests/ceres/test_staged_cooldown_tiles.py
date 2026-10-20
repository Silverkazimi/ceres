""": after a confirmed signing on a plot, a waiting (cooling) staged stake on the same plot keeps its tile."""
from __future__ import annotations

from tests.ceres.vm_context import run_in_module_context

BODY = r"""
BOARD_GENERATION = 1; CURRENT_PLAYER = 'player0005';
playerQueryAppend = (u) => u; refreshBoardTotals = () => {}; renderCheckoutSurface = () => {}; setConfirmationBanner = () => {}; setConfirmationBannerState = () => {};
updateMountedRow = () => {};
const D = 'D-001-01-001';
const live = () => ({ deed_uid: D, plot: 1, bonus: {}, capacity: { worker_slots: 5 }, staking_facts: { is_power_core_staked: true }, assets_loaded: true,
  workers: [{ asset_ref: 'card-A', uid: 'card-A', name: 'Alpha', slot: 1 }] });
const row = live(); BOARD = { player: 'player0005', plots: [row] }; CURRENT_ROWS = [row];
const cooldown = { ready: false, state: 'cooling', ready_at: new Date(Date.now() + 36*3600e3).toISOString() };
CERES_STAGE.push({ deed_uid: D, group_id: 'worker', slot_index: 0, action: 'unstake', asset_ref: 'card-A', _asset_name: 'Alpha' });
CERES_STAGE.push({ deed_uid: D, group_id: 'worker', slot_index: 1, action: 'stake', asset_ref: 'card-B', cooldown, _asset_name: 'Beta', _asset: { asset_ref: 'card-B', uid: 'card-B', name: 'Beta', cooldown } });
applyStageToRow(row, { force: true });
const occ = (r) => editorGroups(r).find(g => g.id === 'worker').slots.map(s => s ? s.asset_ref : null);
const review = { stagedSnapshot: checkoutSignableEntries() };
const chunk = { txId: 'tx1', operations: [['custom_json', { json: JSON.stringify({ deed_uid: D, unstake: { cards: ['card-A'] } }) }]] };
markBroadcastedChunk(review, chunk, 'tx1');
clearConfirmedChunkEntries(review, chunk);
const fresh = live(); fresh.workers = [];
fetch = async () => ({ ok: true, json: async () => fresh });
(async () => {
  await refreshConfirmedPlot(D);
  const slots = occ(BOARD.plots[0]);
  if (!slots.includes('card-B')) throw Error('waiting staged card left its tile: ' + JSON.stringify(slots));
  if (!JSON.stringify(CERES_STAGE.map(e => e.asset_ref)).includes('card-B')) throw Error('staged entry lost');
  console.log('OK ' + JSON.stringify(slots));
})().catch(e => { console.error(e && e.stack); process.exit(1); });
"""


def test_waiting_staged_stake_keeps_its_tile_after_a_confirmed_signing():
    result = run_in_module_context(BODY)
    assert "OK" in result.stdout
