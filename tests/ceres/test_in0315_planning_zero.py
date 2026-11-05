"""IN-0315. The real board modules in the shared VM sandbox (no browser): the collapsed cells and the
totals strip read the plan only while planning, and Live returns exactly what it showed before."""
from __future__ import annotations

from tests.ceres.vm_context import run_in_module_context

BODY = r"""
(async () => {
  const fail = (message) => { throw new Error(message); };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const strip = { innerHTML: '', hidden: true };
  document.getElementById = (id) => (id === 'totals-strip' ? strip : null);
  document.body.dataset = {};
  const calls = [];
  let mode = 'ok';
  fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    if (mode === 'fail') return { ok: false, status: 500, json: async () => ({ detail: { code: 'stub_failure' } }) };
    return { ok: true, status: 200, json: async () => ({ generated_at: body.capture_generated_at, assets: {},
      rows: body.rows.map((row) => ({ deed_uid: row.deed_uid, coverage: 'complete', harvest_pp: 8181, construction_pp: 8080,
        base_pp: 6060, base_pp_after_cap: 6060, multiplier: 2.5, work_per_hour: 12, grain_cost_per_hour: 3,
        wood_cost_per_hour: null, stone_cost_per_hour: null, iron_cost_per_hour: null,
        capacity: { display: '1/5', worker_count: 1, runi_staked: false, card_capacity: 5 } })) }) };
  };
  const liveRow = (uid, plot, harvest) => ({ deed_uid: uid, plot, territory: 'Pristine', region: 'PR-1', rarity: 'Rare',
    plot_status: 'Natural', terrain: 'Forest', worksite_type: 'Grain Farm', resource: 'GRAIN',
    harvest_pp: harvest, construction_pp: harvest - 11, base_pp: 3333, base_pp_after_cap: 3333, multiplier: 1.75,
    work_per_hour: 77.5, grain_cost_per_hour: 4.25, wood_cost_per_hour: null, stone_cost_per_hour: null, iron_cost_per_hour: null,
    capacity: { display: '4/5', worker_count: 4, card_capacity: 5, runi_staked: false, open_slots: 1 },
    dec: { needed: 987, shortfall: 0, committed: 987 }, workers: [], worker_names: ['Live Worker'],
    pp_tooltip: { workers: ['Live Worker'], production_modifiers: { totem_boost: 0.75 } },
    bonus: { totem: { name: 'Live Totem', rarity: 'epic', boost: 0.75 }, title: null }, badges: [], notices: [] });
  const rowA = liveRow('deed-a', '1-2-3', 4242);
  const rowB = liveRow('deed-b', '1-2-4', 5353);
  BOARD = { player: 'player0012', generated_at: '2026-10-30T10:00:00Z', plots: [rowA, rowB], notices: [],
    totals: { base_pp: 6666, harvest_pp: 9595, dec_needed: 1974, plots_idle: 0, plots: 2 } };
  const liveFigures = ['4,242', '4,231', '3,333', '1.75x', '987', '4/5', '5,353'];
  const cells = (row, i) => plotRowCellsHtml(row, i);

  // Live: the board's own figures.
  const liveA = cells(rowA, 0), liveB = cells(rowB, 1);
  renderTotalsStrip();
  const liveStrip = strip.innerHTML;
  if (!liveA.includes('4,242') || !liveStrip.includes('9,595')) fail('live fixture does not render its figures');

  // 1. A new plan: every row and every total from the plan only -- all 0, no live figure, no request.
  document.body.dataset.boardMode = 'planning';
  boardPlanningStore();
  const emptyA = cells(rowA, 0), emptyB = cells(rowB, 1);
  for (const [name, html] of [['A', emptyA], ['B', emptyB]]) {
    for (const figure of liveFigures) if (html.includes(`>${figure}<`) || html.includes(`>${figure} `)) fail(`row ${name} shows live ${figure}: ${html}`);
    if (html.includes('data-row-plan-status')) fail(`row ${name} gained a planning marker`);
    if (!html.includes('data-pp-exact="0"')) fail(`row ${name} PP is not 0`);
    if (!html.includes('0/5')) fail(`row ${name} workers are not 0/5`);
    if (html.includes('Live Totem')) fail(`row ${name} shows the live Totem`);
  }
  renderTotalsStrip();
  for (const label of ['Base PP', 'Total PP', 'DEC stake']) {
    if (!strip.innerHTML.includes(`${label}: <strong>0</strong>`)) fail(`${label} total is not 0: ${strip.innerHTML}`);
  }
  if (!strip.innerHTML.includes('Idle plots: <strong>2</strong>')) fail(`a new plan has every plot idle: ${strip.innerHTML}`);
  await wait(400);
  if (calls.length) fail('an empty plan asked the evaluator');

  // 2. One planned card: only that row and the totals change.
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-a', group_id: 'worker', slot_index: 0, asset_ref: 'w-1',
    asset: { asset_ref: 'w-1', name: 'Planned Worker' } });
  const pendingA = cells(rowA, 0);
  if (!pendingA.includes('Unknown') || pendingA.includes('4,242')) fail('a pending planned row must say Unknown, never live');
  renderTotalsStrip();
  if (!strip.innerHTML.includes('Total PP: <strong>Unknown</strong>')) fail(`pending total must be Unknown: ${strip.innerHTML}`);
  await wait(400);
  if (calls.length !== 1 || calls[0].body.rows.length !== 1 || calls[0].body.rows[0].deed_uid !== 'deed-a') fail(`one request for the one plot: ${JSON.stringify(calls)}`);
  const plannedA = cells(rowA, 0);
  if (!plannedA.includes('8,181')) fail(`planned row lacks its planned Total PP: ${plannedA}`);
  if (cells(rowB, 1) !== emptyB) fail('the untouched row changed');
  renderTotalsStrip();
  for (const text of ['Base PP: <strong>6,060</strong>', 'Total PP: <strong>8,181</strong>', 'DEC stake: <strong>Unknown</strong>', 'Idle plots: <strong>1</strong>']) {
    if (!strip.innerHTML.includes(text)) fail(`totals lack ${text}: ${strip.innerHTML}`);
  }

  // 3. A failed evaluation for a new roster: the row and the totals say Unknown, never live, never 0.
  mode = 'fail';
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-a', group_id: 'worker', slot_index: 0, asset_ref: 'w-2',
    asset: { asset_ref: 'w-2', name: 'Other Worker' } });
  cells(rowA, 0);
  await wait(400);
  if (calls.length !== 2) fail(`the new roster asks once: ${calls.length}`);
  const failedA = cells(rowA, 0);
  if (!failedA.includes('data-row-plan-status="unknown"') || failedA.includes('4,242') || failedA.includes('8,181')) fail(`failed row: ${failedA}`);
  renderTotalsStrip();
  if (!strip.innerHTML.includes('Total PP: <strong>Unknown</strong>') || !strip.innerHTML.includes('Base PP: <strong>Unknown</strong>')) fail(`failed totals: ${strip.innerHTML}`);
  if (strip.innerHTML.includes('9,595') || strip.innerHTML.includes('6,666')) fail('totals fell back to live');

  // 4. Menu > Live: the live rows and totals exactly as before.
  delete document.body.dataset.boardMode;
  if (cells(rowA, 0) !== liveA || cells(rowB, 1) !== liveB) fail('Live does not restore the live rows exactly');
  renderTotalsStrip();
  if (strip.innerHTML !== liveStrip) fail('Live does not restore the live totals exactly');
  if (rowA.harvest_pp !== 4242 || BOARD.totals.harvest_pp !== 9595) fail('the live data was mutated');
  process.exit(0); // the board's own module-scope timers would keep node alive
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_new_plan_is_all_zero_one_card_changes_its_row_and_totals_and_live_round_trips():
    result = run_in_module_context(BODY, check=False)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-2000:]
