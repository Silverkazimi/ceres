// IN-0267 phase 1: the browser plan store, row projection and evaluator scheduler (ceres/static/board-plans.js).
// In-memory storage and fake timers only: nothing touches ~/.ceres, the network or a real clock handle.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  boardPlanAssetStatus, boardPlanEmptyRow, boardPlanPlacementsFromLive, boardPlanRowSignature, boardPlanStoreKey, boardPlanTotals, boardPlanV2Key, createBoardPlanEvaluator,
  createBoardPlanStore, projectBoardRow, readSelectedBoardPlan,
} from '../../ceres/static/board-plans.js';

function memoryStorage() {
  const data = new Map();
  return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), data };
}

function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    setTimer: (fn) => { const id = next++; pending.set(id, fn); return id; },
    clearTimer: (id) => pending.delete(id),
    async run() { while (pending.size) { const [id, fn] = pending.entries().next().value; pending.delete(id); fn(); await new Promise((r) => setImmediate(r)); } },
    size: () => pending.size,
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('v1 migration keeps placement bytes and the old key', () => {
  const storage = memoryStorage();
  const old = { version: 1, account: 'player0012', revision: 9, selected_id: 'legacy-id', plans: [
    { id: 'legacy-id', name: 'Plan 1', account: 'player0012', revision: 3, placements: [
      { deed_uid: 'plot-a', group_id: 'worker', slot_index: 0, asset_ref: 'card-a', name: 'Named Card' }] }] };
  const raw = JSON.stringify(old);
  storage.setItem(boardPlanStoreKey('player0012'), raw);
  const snapshot = createBoardPlanStore(storage, 'player0012').load();
  assert.equal(snapshot.selectedId, 'legacy-id');
  assert.deepEqual(snapshot.plans[0].placements, old.plans[0].placements);
  assert.equal(storage.getItem(boardPlanStoreKey('player0012')), raw);
  assert.equal(JSON.parse(storage.getItem(boardPlanV2Key('player0012'))).schema_version, 2);
});

test('named setup CRUD, tombstone, undo, and duplicate without run state', async () => {
  const storage = memoryStorage();
  const store = createBoardPlanStore(storage, 'player0012'); store.load();
  const original = store.snapshot().selectedId;
  await store.create('Grain');
  const grain = store.snapshot().selectedId;
  await store.rename(grain, 'Grain farm');
  await store.setPlacement({ deed_uid: 'plot-a', group_id: 'worker', slot_index: 0, asset_ref: 'card-a', name: 'Card A' });
  await store.duplicate(grain);
  const copy = store.snapshot().selectedId;
  assert.notEqual(copy, grain);
  assert.equal(store.selected().name, 'Grain farm copy');
  assert.equal(store.selected().run_state, undefined);
  assert.equal(store.selected().placements.length, 1);
  await store.select(original);
  const deleted = await store.delete(grain);
  assert.ok(store.snapshot().tombstones[grain]);
  await store.undoDelete(deleted.deleted);
  assert.equal(store.snapshot().selectedId, grain);
  assert.equal(store.snapshot().tombstones[grain], undefined);
});

test('different setup edits merge by stable id across tabs', async () => {
  const storage = memoryStorage();
  const setup = createBoardPlanStore(storage, 'player0012'); setup.load();
  const original = setup.snapshot().selectedId;
  await setup.create('Other'); const other = setup.snapshot().selectedId;
  const a = createBoardPlanStore(storage, 'player0012'); a.load();
  const b = createBoardPlanStore(storage, 'player0012'); b.load();
  await a.rename(original, 'Original renamed');
  await b.rename(other, 'Other renamed');
  const saved = createBoardPlanStore(storage, 'player0012').load();
  assert.equal(saved.plans.length, 2);
  assert.equal(saved.plans.find((plan) => plan.id === original).name, 'Original renamed');
  assert.equal(saved.plans.find((plan) => plan.id === other).name, 'Other renamed');
});

test('account keys stay isolated and commits enter the account lock', async () => {
  const storage = memoryStorage();
  let lockCalls = 0;
  const owner = createBoardPlanStore(storage, 'player0012', { lock: (task) => { lockCalls++; return task(); } });
  const other = createBoardPlanStore(storage, 'other01');
  owner.load(); other.load();
  await owner.create('My setup');
  assert.equal(lockCalls, 1);
  assert.equal(other.snapshot().plans.length, 1);
  assert.equal(other.snapshot().plans[0].name, 'Plan 1');
  assert.notEqual(boardPlanV2Key('player0012'), boardPlanV2Key('other01'));
});

test('concurrent delete and edit retain the edited setup as a conflict copy', async () => {
  const storage = memoryStorage();
  const seed = createBoardPlanStore(storage, 'player0012'); seed.load();
  await seed.create('Other');
  const id = seed.snapshot().selectedId;
  const deleting = createBoardPlanStore(storage, 'player0012'); deleting.load();
  const editing = createBoardPlanStore(storage, 'player0012'); editing.load();
  await editing.rename(id, 'Edited in other tab');
  await deleting.delete(id);
  const saved = createBoardPlanStore(storage, 'player0012').load();
  assert.ok(saved.tombstones[id]);
  assert.equal(saved.plans.some((plan) => plan.id === id), false);
  assert.equal(saved.plans.filter((plan) => plan.name.includes('conflict copy')).length, 0,
    'the delete refreshes first and removes the current durable version');
});

test('undo deletion survives an unrelated tab save', async () => {
  const storage = memoryStorage();
  const initial = createBoardPlanStore(storage, 'player0012'); initial.load();
  const first = initial.snapshot().selectedId;
  await initial.create('Other');
  const other = initial.snapshot().selectedId;
  const deleting = createBoardPlanStore(storage, 'player0012'); deleting.load();
  const deleted = await deleting.delete(other);
  const elsewhere = createBoardPlanStore(storage, 'player0012'); elsewhere.load();
  await elsewhere.rename(first, 'Renamed elsewhere');
  await deleting.undoDelete(deleted.deleted);
  const saved = createBoardPlanStore(storage, 'player0012').load();
  assert.equal(saved.tombstones[other], undefined);
  assert.equal(saved.plans.find((plan) => plan.id === other).name, 'Other');
  assert.equal(saved.plans.find((plan) => plan.id === first).name, 'Renamed elsewhere');
});

test('quota failure leaves durable revision, holds unsaved edit, and permits retry', async () => {
  const storage = memoryStorage();
  const store = createBoardPlanStore(storage, 'player0012'); store.load();
  const saved = storage.getItem(boardPlanV2Key('player0012'));
  const set = storage.setItem;
  let fail = true;
  storage.setItem = (key, value) => { if (fail && key === boardPlanV2Key('player0012')) throw new Error('quota'); set(key, value); };
  await assert.rejects(store.rename(store.snapshot().selectedId, 'Unsaved'), /quota/);
  assert.equal(storage.getItem(boardPlanV2Key('player0012')), saved);
  assert.equal(store.snapshot().dirty, true);
  assert.throws(() => store.create('Cannot switch'), /unsaved changes/);
  fail = false;
  await store.persist();
  assert.equal(readSelectedBoardPlan(storage, 'player0012').name, 'Unsaved');
});

test('stale second store places into the latest selected setup', async () => {
  const storage = memoryStorage();
  const a = createBoardPlanStore(storage, 'player0012'); a.load();
  const x = a.snapshot().selectedId;
  await a.create('Y'); const y = a.snapshot().selectedId;
  await a.select(x);
  const b = createBoardPlanStore(storage, 'player0012'); b.load();
  await b.select(y);
  await a.setPlacement({ deed_uid: 'plot-y', group_id: 'worker', slot_index: 0, asset_ref: 'asset-y' });
  assert.equal(a.snapshot().selectedId, y);
  assert.equal(a.selected().placements[0].asset_ref, 'asset-y');
  assert.equal(createBoardPlanStore(storage, 'player0012').load().plans.find((plan) => plan.id === x).placements.length, 0);
});

test('corrupt primary can restore the verified backup; newer schema cannot', async () => {
  const storage = memoryStorage();
  const seed = createBoardPlanStore(storage, 'player0012'); seed.load();
  const saved = createBoardPlanStore(storage, 'player0012'); saved.load();
  await saved.rename(saved.snapshot().selectedId, 'Last saved');
  storage.setItem(`${boardPlanV2Key('player0012')}:backup`, storage.getItem(boardPlanV2Key('player0012')));
  storage.setItem(boardPlanV2Key('player0012'), '{broken');
  const recover = createBoardPlanStore(storage, 'player0012');
  assert.equal(recover.load().readOnly, true);
  assert.equal(recover.restoreBackup().readOnly, false);
  assert.equal(recover.selected().name, 'Last saved');
  storage.setItem(boardPlanV2Key('player0012'), JSON.stringify({ schema_version: 3, account: 'player0012' }));
  const future = createBoardPlanStore(storage, 'player0012');
  assert.equal(future.load().readOnly, true);
  assert.throws(() => future.restoreBackup(), /No recoverable backup/);
});

test('verified main write fits alongside the previous one-envelope backup', async () => {
  const storage = memoryStorage();
  const baseSet = storage.setItem;
  const limit = 2;
  storage.setItem = (key, value) => {
    const next = new Map(storage.data); next.set(key, String(value));
    const envelopes = [...next].filter(([k]) => k.startsWith(boardPlanV2Key('player0012'))).length;
    if (envelopes > limit) throw new Error('quota');
    baseSet(key, value);
  };
  const store = createBoardPlanStore(storage, 'player0012'); store.load();
  await store.rename(store.snapshot().selectedId, 'Fits');
  assert.equal(store.selected().name, 'Fits');
  assert.ok(storage.getItem(`${boardPlanV2Key('player0012')}:backup`));
});

test('undo of deleting the last setup removes only its unchanged empty replacement; writes prune old tombstones', async () => {
  const storage = memoryStorage();
  const store = createBoardPlanStore(storage, 'player0012'); store.load();
  const deleted = await store.delete(store.snapshot().selectedId);
  await store.undoDelete(deleted.deleted);
  assert.equal(store.snapshot().plans.length, 1);
  assert.equal(store.snapshot().plans[0].name, 'Plan 1');
  const envelope = JSON.parse(storage.getItem(boardPlanV2Key('player0012')));
  envelope.tombstones['old-plan'] = new Date(Date.now() - 31 * 86400000).toISOString();
  envelope.revision += 1;
  storage.setItem(boardPlanV2Key('player0012'), JSON.stringify(envelope));
  store.refresh();
  await store.rename(store.snapshot().selectedId, 'Prune');
  assert.equal(store.snapshot().tombstones['old-plan'], undefined);
});

test('unsupported v2 refuses mutation and corrupt primary retains previous good revision', async () => {
  const storage = memoryStorage();
  storage.setItem(boardPlanV2Key('player0012'), JSON.stringify({ schema_version: 3, account: 'player0012' }));
  const future = createBoardPlanStore(storage, 'player0012');
  assert.match(future.load().readOnlyReason, /newer version.*read-only/);
  assert.throws(() => future.create('No'), /read-only/);
  storage.data.clear();
  const store = createBoardPlanStore(storage, 'player0012'); store.load();
  await store.rename(store.snapshot().selectedId, 'Good');
  storage.setItem(boardPlanV2Key('player0012'), '{broken');
  const recovered = createBoardPlanStore(storage, 'player0012');
  const snapshot = recovered.load();
  assert.equal(snapshot.readOnly, true);
  assert.equal(snapshot.plans[0].name, 'Plan 1');
  assert.throws(() => recovered.rename(snapshot.selectedId, 'No'), /read-only/);
});

test('Start from live copies loaded occupants with names and no live mutation', () => {
  const live = [{ deed_uid: 'plot-a', workers: [
    { slot: 1, asset_ref: 'card-a', name: 'Named Worker' }, { slot: 0, asset_ref: 'runi-a', name: 'Runi' }],
    bonus: { totem: { asset_ref: 'totem-a', name: 'Totem' }, title: null, power_core: null } }];
  const before = JSON.stringify(live);
  const placements = boardPlanPlacementsFromLive(live);
  assert.deepEqual(placements.map((p) => [p.group_id, p.slot_index, p.asset.name]),
    [['worker', 0, 'Named Worker'], ['runi', 0, 'Runi'], ['totem', 0, 'Totem']]);
  assert.equal(JSON.stringify(live), before);
});

test('creates one clean autosaved Plan 1 per account, separate from the live stage store', async () => {
  const storage = memoryStorage();
  storage.setItem('ceres:stage-plan:v1:player0012', '{"live":true}');
  const store = createBoardPlanStore(storage, 'player0012');
  const created = store.load();
  assert.equal(created.plans.length, 1);
  assert.equal(created.plans[0].name, 'Plan 1');
  assert.deepEqual(created.plans[0].placements, []);
  assert.equal(boardPlanStoreKey('player0012'), 'ceres:board-plans:v1:player0012');
  assert.ok(storage.data.has('ceres:board-plans:v2:player0012'));
  await store.setPlacement({ deed_uid: 'plot-a', group_id: 'worker', slot_index: 0, asset_ref: 'asset-a',
    asset: { name: 'Card A', image: 'a.png', cooldown: { state: 'ready', ready: true }, total_harvest_pp: 99 } });
  const reloaded = createBoardPlanStore(storage, 'player0012').load();
  const saved = reloaded.plans[0].placements[0];
  assert.equal(saved.asset_ref, 'asset-a');
  assert.equal(saved.asset.name, 'Card A');
  assert.equal(saved.asset.cooldown, undefined, 'no game state is saved in a plan');
  assert.equal(saved.asset.total_harvest_pp, undefined);
  assert.equal(storage.getItem('ceres:stage-plan:v1:player0012'), '{"live":true}', 'the live stage store is untouched');
  assert.equal(readSelectedBoardPlan(storage, 'player0012').placements.length, 1);
});

test('one asset per plan, one asset per slot, replace and remove, concurrent edits make a copy', async () => {
  const storage = memoryStorage();
  createBoardPlanStore(storage, 'player0012').load();
  const writerA = createBoardPlanStore(storage, 'player0012'); writerA.load();
  const writerB = createBoardPlanStore(storage, 'player0012'); writerB.load();
  const originalId = writerA.snapshot().selectedId;
  await writerA.setPlacement({ deed_uid: 'plot-a', group_id: 'worker', slot_index: 0, asset_ref: 'asset-a' });
  await writerB.setPlacement({ deed_uid: 'plot-b', group_id: 'worker', slot_index: 0, asset_ref: 'asset-b' });
  assert.equal(createBoardPlanStore(storage, 'player0012').load().plans.length, 1, 'both tabs target the stored selected setup');
  const current = createBoardPlanStore(storage, 'player0012'); current.load();
  await current.select(originalId);
  await assert.rejects(current.setPlacement({ deed_uid: 'plot-b', group_id: 'worker', slot_index: 1, asset_ref: 'asset-a' }), /already planned/);
  await current.setPlacement({ deed_uid: 'plot-a', group_id: 'worker', slot_index: 0, asset_ref: 'asset-c' });
  let plan = readSelectedBoardPlan(storage, 'player0012');
  assert.deepEqual(plan.placements.map((p) => p.asset_ref).sort(), ['asset-b', 'asset-c'], 'choosing for a planned slot replaces it in the selected setup');
  assert.equal(await current.removePlacement({ deed_uid: 'plot-a', group_id: 'worker', slot_index: 0 }), true);
  plan = readSelectedBoardPlan(storage, 'player0012');
  assert.deepEqual(plan.placements.map((p) => p.asset_ref), ['asset-b']);
  await assert.rejects(current.setPlacement({ deed_uid: 'plot-a', group_id: 'nonsense', slot_index: 0, asset_ref: 'x' }), /Invalid placement/);
});

const LIVE_ROW = {
  deed_uid: 'a', plot: '1-2-3', harvest_pp: 42, construction_pp: 40, base_pp: 30, base_pp_after_cap: 30, multiplier: 1.5, work_per_hour: 7,
  grain_cost_per_hour: 0.3, wood_cost_per_hour: 0.2, stone_cost_per_hour: null, iron_cost_per_hour: null,
  capacity: { display: '1/5', worker_count: 1, potential_card_capacity: 5, card_capacity: 5, runi_staked: false },
  dec: { needed: 120, shortfall: 20, committed: 100 }, workers: [{ name: 'Live Worker', slot: 1 }], worker_names: ['Live Worker'],
  pp_tooltip: { workers: ['Live Worker'], production_modifiers: { totem_boost: 0.5 } },
  bonus: { totem: { name: 'Live Totem' }, title: null }, _live_bonus: { totem: { name: 'Live Totem' } },
};
const LIVE_ROW_COPY = JSON.stringify(LIVE_ROW);

// IN-0315 (owner: "A new plan should show all stats at 0"): in planning the plan is the only truth.
test('IN-0315: a row the plan does not touch is a clean sheet, every stat 0 or "—", no live figure', () => {
  const empty = projectBoardRow(LIVE_ROW, { placements: [{ deed_uid: 'other', group_id: 'worker', slot_index: 0, asset_ref: 'x' }] });
  assert.equal(empty.changed, false);
  assert.equal(empty.status, 'empty');
  assert.notEqual(empty.row, LIVE_ROW, 'never the live row object in planning');
  assert.equal(empty.row.planning, undefined, 'no planning marker: the ordinary cells render it');
  for (const field of ['harvest_pp', 'construction_pp', 'base_pp', 'base_pp_after_cap', 'work_per_hour']) assert.equal(empty.row[field], 0, field);
  assert.equal(empty.row.multiplier, null, 'no boost on an empty sheet: the existing "—"');
  assert.equal(empty.row.grain_cost_per_hour, 0);
  assert.equal(empty.row.wood_cost_per_hour, 0, 'an input this worksite uses reads 0');
  assert.equal(empty.row.stone_cost_per_hour, null, 'an input it does not use keeps the existing "—"');
  assert.equal(empty.row.dec.needed, 0);
  assert.equal(empty.row.capacity.display, '0/5');
  assert.equal(empty.row.capacity.worker_count, 0);
  assert.deepEqual(empty.row.workers, []);
  assert.deepEqual(empty.row.pp_tooltip, { workers: [] });
  assert.deepEqual(empty.row.bonus, { totem: null, title: null, power_core: null });
  assert.equal(empty.row._live_bonus, undefined);
  assert.equal(empty.row.plot, '1-2-3', 'plot facts stay');
  const noPlan = projectBoardRow(LIVE_ROW, null);
  assert.equal(noPlan.status, 'empty', 'planning without a readable plan is still a clean sheet');
  assert.equal(noPlan.row.harvest_pp, 0);
  assert.equal(JSON.stringify(LIVE_ROW), LIVE_ROW_COPY, 'the live row is never mutated (Live restores it exactly)');
  assert.deepEqual(boardPlanEmptyRow(LIVE_ROW), noPlan.row);
});

const ROW_B = { ...LIVE_ROW, deed_uid: 'b', plot: '1-2-4', harvest_pp: 900, base_pp: 500 };
const plannedAnswer = { coverage: 'complete', harvest_pp: 81, construction_pp: 80, base_pp: 60, base_pp_after_cap: 60, multiplier: 2.5,
  work_per_hour: 12, capacity: { display: '1/5', worker_count: 1 } };

test('IN-0315: a new plan totals 0; one placed card changes only its row and the totals', () => {
  const asked = [];
  const project = (plan, answers) => (row) => { asked.push(row.deed_uid); return projectBoardRow(row, plan, { evaluation: answers[row.deed_uid] }); };
  const fresh = { placements: [] };
  const zero = boardPlanTotals([LIVE_ROW, ROW_B], fresh, project(fresh, {}));
  assert.deepEqual({ base: zero.base_pp, total: zero.harvest_pp, dec: zero.dec_needed, idle: zero.plots_idle, plots: zero.plots, workers: zero.workers_staked },
    { base: 0, total: 0, dec: 0, idle: 2, plots: 2, workers: 0 });
  assert.deepEqual(asked, [], 'an empty plan projects nothing for totals: no evaluate question');
  const plan = { placements: [{ deed_uid: 'a', group_id: 'worker', slot_index: 0, asset_ref: 'w1', asset: { name: 'Worker' } }] };
  const before = projectBoardRow(ROW_B, fresh);
  const after = projectBoardRow(ROW_B, plan);
  assert.deepEqual(after, before, 'the untouched row is the same clean sheet before and after the placement');
  const placed = boardPlanTotals([LIVE_ROW, ROW_B], plan, project(plan, { a: plannedAnswer }));
  assert.deepEqual(asked, ['a'], 'only the planned plot is projected');
  assert.equal(placed.harvest_pp, 81);
  assert.equal(placed.base_pp, 60);
  assert.equal(placed.plots_idle, 1);
  assert.equal(placed.workers_staked, 1);
  assert.equal(placed.dec_needed, null, 'planned DEC is not worked out: Unknown, never live');
});

test('IN-0315: a pending or failed evaluation makes the totals Unknown, never the live figure', () => {
  const plan = { placements: [{ deed_uid: 'a', group_id: 'totem', slot_index: 0, asset_ref: 't1' },
    { deed_uid: 'b', group_id: 'worker', slot_index: 0, asset_ref: 'w1' }] };
  const failed = boardPlanTotals([LIVE_ROW, ROW_B], plan, (row) => projectBoardRow(row, plan, {
    evaluation: row.deed_uid === 'a' ? plannedAnswer : { coverage: 'unknown', reason: 'request_failed' } }));
  assert.equal(failed.harvest_pp, null);
  assert.equal(failed.base_pp, null);
  assert.equal(failed.unknown_plots, 1);
  const pending = boardPlanTotals([LIVE_ROW, ROW_B], plan, (row) => projectBoardRow(row, plan));
  assert.equal(pending.harvest_pp, null);
  assert.equal(pending.unknown_plots, 2);
  const failedRow = projectBoardRow(ROW_B, plan, { evaluation: { coverage: 'unknown', reason: 'request_failed' } });
  for (const field of ['harvest_pp', 'base_pp', 'work_per_hour', 'multiplier']) assert.equal(failedRow.row[field], null, field);
  assert.equal(failedRow.row.dec.needed, null, 'no live DEC under a changed row: no value, as its cell says Unknown');
  assert.deepEqual(failedRow.row.workers, [], 'no live worker under a changed row');
});

test('a changed row never carries live figures until a complete answer for its roster arrives', () => {
  const plan = { placements: [{ deed_uid: 'a', group_id: 'totem', slot_index: 0, asset_ref: 'tot', asset: { name: 'Legendary Totem', rarity: 'legendary' } }] };
  const pending = projectBoardRow(LIVE_ROW, plan);
  assert.equal(pending.status, 'pending');
  assert.equal(pending.row.harvest_pp, null);
  assert.equal(pending.row.multiplier, null);
  assert.equal(pending.row.capacity.display, null);
  assert.equal(pending.row._live_bonus, undefined, 'the live bonus no longer shows through');
  assert.equal(pending.row.bonus.totem.name, 'Legendary Totem');
  assert.equal(pending.row.bonus.title, null, 'a clean sheet has no title unless planned');
  assert.equal(LIVE_ROW.harvest_pp, 42, 'the live row is never mutated');
  const unknown = projectBoardRow(LIVE_ROW, plan, { evaluation: { coverage: 'unknown', reason: 'plot_detail_missing' } });
  assert.equal(unknown.status, 'unknown');
  assert.equal(unknown.row.planning.reason, 'plot_detail_missing');
  assert.equal(unknown.row.harvest_pp, null);
  const failedDetail = projectBoardRow(LIVE_ROW, plan, { coverageComplete: false, evaluation: { coverage: 'complete', harvest_pp: 5 } });
  assert.equal(failedDetail.status, 'unknown');
  const planned = projectBoardRow(LIVE_ROW, plan, { evaluation: { coverage: 'complete', harvest_pp: 81, construction_pp: 80, base_pp: 60,
    base_pp_after_cap: 60, multiplier: 2.5, work_per_hour: 12, capacity: { display: '0/5', worker_count: 0 } } });
  assert.equal(planned.status, 'planned');
  assert.equal(planned.row.harvest_pp, 81);
  assert.equal(planned.row.multiplier, 2.5);
  assert.equal(planned.row.capacity.display, '0/5');
  assert.equal(planned.row.grain_cost_per_hour, null, 'a value the answer lacks is not taken from live');
  assert.deepEqual(planned.row.pp_tooltip, { workers: [] }, 'no live tooltip detail under planned values');
});

// IN-0315 round 2: sort, badges and the power warning read these projected fields; none may come from live.
test('the projection carries plan-only badges, power and sort values', () => {
  const live = { ...LIVE_ROW, badges: ['unpowered', 'over_cap'], notices: [{ code: 'unpowered_worker', message: 'Worker slot 1 (Live) has no power' }],
    region_efficiency: 0.5, ability_holders: [{ asset_ref: 'h', codes: ['ENERGIZED'] }], powered: true,
    pending_change: { to: 'WOOD' }, staking_facts: { is_energized: true, is_power_core_staked: true, is_runi_staked: true } };
  const empty = projectBoardRow(live, { placements: [] }).row;
  assert.deepEqual(empty.badges, ['idle'], 'a clean sheet has nothing staked: Idle, never the live badges');
  assert.deepEqual([empty.notices, empty.ability_holders, empty.region_efficiency, empty.powered], [[], [], null, null]);
  assert.deepEqual([empty.staking_facts.is_energized, empty.staking_facts.is_power_core_staked, empty.staking_facts.is_runi_staked], [false, false, false]);
  assert.equal(empty.planning_view, true, 'the badge code ignores the live stage for a projected row');
  assert.deepEqual(empty.pending_change, { to: 'WOOD' }, 'the Worksite cell keeps the plot fact');
  const plan = { placements: [{ deed_uid: 'a', group_id: 'worker', slot_index: 0, asset_ref: 'w1' }] };
  const pending = projectBoardRow(live, plan).row;
  assert.deepEqual(pending.badges, [], 'a pending planned row shows no badge');
  assert.deepEqual([pending.capacity.worker_count, pending.capacity.open_slots, pending.dec.needed, pending.powered], [null, null, null, null],
    'a pending planned row sorts as no value');
  const planned = projectBoardRow(live, plan, { evaluation: { ...plannedAnswer, powered: false, base_pp_cap_reached: true,
    capacity: { display: '1/5', worker_count: 1, card_capacity: 5, runi_staked: false } } }).row;
  assert.deepEqual(planned.badges, ['unpowered', 'over_cap'], 'a planned row badges follow the plan answer');
  assert.equal(planned.powered, false);
  assert.equal(planned.capacity.open_slots, 4, 'open slots follow the planned roster');
  assert.equal(planned.dec.needed, null, 'planned DEC is Unknown');
  assert.deepEqual(projectBoardRow(live, plan, { evaluation: { ...plannedAnswer, powered: true,
    capacity: { display: '0/5', worker_count: 0, card_capacity: 5 } } }).row.badges, ['idle']);
});

test('asset status comes from the capture answer; Survival and cooldown wait, nothing says Ready', () => {
  assert.equal(boardPlanAssetStatus(null).status, 'unknown');
  assert.equal(boardPlanAssetStatus({ known: false }).status, 'unknown');
  const dead = boardPlanAssetStatus({ known: true, staked: false, cooldown: { state: 'survival_locked', ready: false, ready_at: '2030-01-01T00:00:00Z' } });
  assert.deepEqual([dead.status, dead.label, dead.until], ['waiting', 'Dead in Survival', '2030-01-01T00:00:00Z']);
  const deadStaked = boardPlanAssetStatus({ known: true, staked: true, cooldown: { state: 'survival_locked', ready: false } });
  assert.equal(deadStaked.status, 'waiting', 'Survival wins over staked');
  assert.equal(boardPlanAssetStatus({ known: true, staked: false, cooldown: { state: 'cooling', ready: false, ready_at: 'x' } }).status, 'waiting');
  const staked = boardPlanAssetStatus({ known: true, staked: true, location_deed_uid: 'plot-b', cooldown: { state: 'staked', ready: false } });
  assert.deepEqual([staked.status, staked.locationDeedUid], ['staked', 'plot-b']);
  assert.equal(boardPlanAssetStatus({ known: true, staked: false, cooldown: { state: 'ready', ready: true } }).status, 'available');
  assert.equal(boardPlanAssetStatus({ known: true, staked: false, cooldown: { state: 'unknown', ready: false } }).status, 'waiting');
  for (const state of [dead, staked]) assert.ok(!/ready/i.test(state.label));
});

test('evaluator: debounced, bounded batches of changed rows only, answers reused per exact roster', async () => {
  const timers = fakeTimers();
  const bodies = [];
  const updates = [];
  const evaluator = createBoardPlanEvaluator({
    setTimer: timers.setTimer, clearTimer: timers.clearTimer, batchSize: 50, onUpdate: (deeds) => updates.push(deeds),
    request: async (body) => { bodies.push(body); return { rows: body.rows.map((r) => ({ deed_uid: r.deed_uid, coverage: 'complete', harvest_pp: 1 })), assets: { 'ref-0': { known: true } } }; },
  });
  const placements = (i) => [{ group_id: 'worker', slot_index: 0, asset_ref: `ref-${i}` }];
  for (let i = 0; i < 120; i += 1) evaluator.want(`plot-${i}`, placements(i), 'stamp-1', boardPlanRowSignature(placements(i), 'stamp-1'));
  assert.equal(bodies.length, 0, 'nothing is sent before the debounce');
  await timers.run();
  await settle();
  assert.deepEqual(bodies.map((b) => b.rows.length), [50, 50, 20]);
  assert.ok(bodies.every((b) => b.capture_generated_at === 'stamp-1'));
  assert.deepEqual(Object.keys(bodies[0].rows[0].placements[0]).sort(), ['asset_ref', 'group_id', 'slot_index']);
  assert.equal(updates.flat().length, 120);
  const sig = boardPlanRowSignature(placements(3), 'stamp-1');
  assert.equal(evaluator.result('plot-3', sig).harvest_pp, 1);
  assert.equal(evaluator.result('plot-3', boardPlanRowSignature(placements(3), 'stamp-2')), null, 'another capture is another question');
  assert.equal(evaluator.asset('ref-0').known, true);
  evaluator.want('plot-3', placements(3), 'stamp-1', sig);
  assert.equal(timers.size(), 0, 'an answered roster is not asked again');
  evaluator.reset();
  assert.equal(evaluator.result('plot-3', sig), null);
});

test('evaluator: a failed or partial answer makes exactly those rows unknown, never 0', async () => {
  const timers = fakeTimers();
  let fail = true;
  const evaluator = createBoardPlanEvaluator({
    setTimer: timers.setTimer, clearTimer: timers.clearTimer,
    request: async (body) => {
      if (fail) { const error = new Error('HTTP 500'); error.code = 'http_500'; throw error; }
      return { rows: [{ deed_uid: body.rows[0].deed_uid, coverage: 'complete', harvest_pp: 9 }] };
    },
  });
  const p = [{ group_id: 'totem', slot_index: 0, asset_ref: 't' }];
  evaluator.want('a', p, 's', 'sig-a');
  await timers.run(); await settle();
  assert.deepEqual(evaluator.result('a', 'sig-a'), { deed_uid: 'a', coverage: 'unknown', reason: 'http_500' });
  fail = false;
  evaluator.want('b', p, 's', 'sig-b');
  evaluator.want('c', p, 's', 'sig-c');
  await timers.run(); await settle();
  assert.equal(evaluator.result('b', 'sig-b').harvest_pp, 9);
  assert.deepEqual(evaluator.result('c', 'sig-c'), { deed_uid: 'c', coverage: 'unknown', reason: 'missing_in_answer' });
});

test('evaluator: a superseded capture adopts the newer board once and asks again, never guesses', async () => {
  const timers = fakeTimers();
  let adopted = 0;
  let calls = 0;
  const evaluator = createBoardPlanEvaluator({
    setTimer: timers.setTimer, clearTimer: timers.clearTimer,
    adoptLatest: async () => { adopted += 1; },
    request: async () => { calls += 1; const error = new Error('HTTP 409'); error.code = 'capture_superseded'; throw error; },
  });
  const p = [{ group_id: 'worker', slot_index: 0, asset_ref: 'w' }];
  evaluator.want('a', p, 'old', 'sig-old');
  await timers.run(); await settle();
  assert.equal(adopted, 1);
  assert.equal(evaluator.result('a', 'sig-old'), null, 'the old question is dropped, not answered');
  evaluator.want('a', p, 'old', 'sig-old');
  await timers.run(); await settle();
  assert.equal(adopted, 1, 'the same stale stamp is adopted only once');
  assert.equal(evaluator.result('a', 'sig-old').reason, 'capture_superseded');
  assert.equal(calls, 2);
});
