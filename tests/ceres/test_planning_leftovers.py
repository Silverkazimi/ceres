""". Three readers still took the live
row while Planning was on: the board's sort order, the per-row badges (rail, slide-outs and the row pulse) and the
open plot's power warning. The real board modules in the shared VM sandbox (no browser): each now reads the one
shared plan projection, and Live returns exactly what it showed before."""
from __future__ import annotations

from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]

SETUP = r"""
  const fail = (message) => { throw new Error(message); };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.getElementById = () => null;
  document.body.dataset = {};
  const calls = [];
  let answer = () => ({});
  fetch = async (url, init) => {
    // Only the evaluator is answered; any other read (e.g. the per-plot harvest read) gets a quiet 404.
    if (!String(url).includes('/api/board-plans/evaluate')) return { ok: false, status: 404, json: async () => ({}) };
    const body = JSON.parse(init.body);
    calls.push(body);
    return { ok: true, status: 200, json: async () => ({ generated_at: body.capture_generated_at, assets: {},
      rows: body.rows.map((row) => ({ deed_uid: row.deed_uid, coverage: 'complete', harvest_pp: 100, construction_pp: 90,
        base_pp: 80, base_pp_after_cap: 80, multiplier: 1, work_per_hour: 1, grain_cost_per_hour: 1, powered: true,
        wood_cost_per_hour: null, stone_cost_per_hour: null, iron_cost_per_hour: null,
        capacity: { display: '1/5', worker_count: 1, runi_staked: false, card_capacity: 5 }, ...answer(row.deed_uid) })) }) };
  };
  const liveRow = (uid, plot, harvest, extra = {}) => ({ deed_uid: uid, plot, territory: 'Pristine', region: 'PR-1', rarity: 'Rare',
    plot_status: 'Natural', terrain: 'Forest', worksite_type: 'Grain Farm', resource: 'GRAIN',
    harvest_pp: harvest, construction_pp: harvest - 1, base_pp: harvest - 2, base_pp_after_cap: harvest - 2, multiplier: 1.5,
    work_per_hour: harvest / 10, grain_cost_per_hour: 2, wood_cost_per_hour: null, stone_cost_per_hour: null, iron_cost_per_hour: null,
    capacity: { display: '1/5', worker_count: 1, card_capacity: 5, runi_staked: false, open_slots: 4 },
    dec: { needed: harvest, shortfall: 0, committed: harvest }, workers: [], worker_names: ['Live Worker'],
    pp_tooltip: { workers: [] }, bonus: { totem: null, title: null }, badges: [], notices: [], ...extra });
  const planOn = () => { document.body.dataset.boardMode = 'planning'; };
  const planOff = () => { delete document.body.dataset.boardMode; };
"""


def _run(body: str) -> None:
    script = "(async () => {\n" + SETUP + body + "\n  process.exit(0);\n})().catch((error) => { console.error(error); process.exit(1); });"
    result = run_in_module_context(script, check=False)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-2000:]


def test_sort_follows_the_plan_values_never_the_live_figures():
    _run(r"""
  const rowA = liveRow('deed-a', '1-1-1', 9000);
  const rowB = liveRow('deed-b', '1-1-2', 5000);
  const rowC = liveRow('deed-c', '1-1-3', 1000);
  BOARD = { player: 'player0013', generated_at: '2026-10-30T10:00:00Z', plots: [rowA, rowB, rowC], notices: [], totals: {} };
  S.SORT_KEY = 'harvest_pp'; S.SORT_DIR = -1;
  // table.js render()'s own filter+sort, verbatim (the sandbox's bare `render` name is ambiguous across modules --
  // the same sidestep as test_board_guards.py); the source guard below pins that line.
  matchesFilters = () => true;
  let sorted = [];
  const order = () => { sorted = sortRows(BOARD.plots.filter(matchesFilters));
    return sorted.map((row) => row.deed_uid).join(','); };
  const live = order();
  if (live !== 'deed-a,deed-b,deed-c') fail(`live order: ${live}`);
  planOn();
  boardPlanningStore();
  // Plan C at 100 PP, B pending (no value), A untouched (0): C first, A at 0, the pending row last (the board's null rule).
  answer = (deed) => (deed === 'deed-c' ? {} : { coverage: 'pending_never' });
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-c', group_id: 'worker', slot_index: 0, asset_ref: 'w-c', asset: { name: 'C' } });
  order();
  await wait(400);
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-b', group_id: 'worker', slot_index: 0, asset_ref: 'w-b', asset: { name: 'B' } });
  const planned = order();
  if (planned !== 'deed-c,deed-a,deed-b') fail(`planning must sort by the plan (C 100, A 0, B no value): ${planned}`);
  // Every row in planning reads the plan, the rows returned are still the board's own rows.
  if (sorted[0] !== rowC) fail('the sort must return the board rows, not projections');
  planOff();
  if (order() !== live) fail('Live must sort exactly as before');
  if (rowA.harvest_pp !== 9000) fail('the live data was mutated');
""")


def test_table_sorts_through_the_planning_projection():
    # table.js's render() calls sortRows(rows) with no view, so the planning view is sortRows's own default.
    board = (ROOT / "ceres" / "static" / "board.js").read_text(encoding="utf-8")
    assert "export function sortRows(rows, viewRow = boardPlanningActive() ? boardPlanningViewRow : null) {" in board
    table = (ROOT / "ceres" / "static" / "table.js").read_text(encoding="utf-8")
    assert "CURRENT_ROWS = sortRows(BOARD.plots.filter(matchesFilters));" in table
    # The row pulse at mount and the rail repaint read the same projection as the badge overlay (rowForDeedUid).
    rows = (ROOT / "ceres" / "static" / "rows.js").read_text(encoding="utf-8")
    assert "rowPendingClass(boardPlanningViewRow(row))" in rows
    assert "const row = boardPlanningViewRow(plotsByDeed.get(deedUid));" in rows


def test_badges_rail_slideout_and_pulse_read_the_plan_never_live():
    _run(r"""
  const rowA = liveRow('deed-a', '1-1-1', 4000, { badges: ['unpowered', 'underfilled'], region_efficiency: 0.5,
    notices: [{ code: 'unpowered', message: 'live notice' }], ability_holders: [{ asset_ref: 'h1', name: 'Live Holder', codes: ['ENERGIZED'] }] });
  const rowB = liveRow('deed-b', '1-1-2', 3000, { badges: ['over_cap'] });
  BOARD = { player: 'player0013', generated_at: '2026-10-30T10:00:00Z', plots: [rowA, rowB], notices: [], totals: {} };
  CERES_STAGE.push({ deed_uid: 'deed-a', group_id: 'worker', action: 'stake', asset_ref: 'staged-1', slot_index: 1 });
  const codes = (uid) => badgeCodesFor(rowForDeedUid(uid)).join(',');
  // The row pulse and the rail clock both come from badgeStripStatus() of this row (badge-overlay.js sync, renderRow).
  const pulse = (row) => badgeStripStatus(rowForDeedUid(row.deed_uid)).kind === 'pending';
  const liveCodesA = codes('deed-a'), liveCodesB = codes('deed-b');
  const liveStripA = badgeStripHtml(rowForDeedUid('deed-a'));
  if (!liveCodesA.includes('staged_changes') || !liveCodesA.includes('region_dec') || !liveCodesA.includes('unpowered')) fail(`live fixture: ${liveCodesA}`);
  if (!pulse(rowA)) fail('live fixture: a staged change pulses the row');

  // A new plan: nothing staked anywhere -- Idle only (the totals' idle count), no live badge, no live stage, no pulse.
  planOn();
  boardPlanningStore();
  for (const uid of ['deed-a', 'deed-b']) if (codes(uid) !== 'idle') fail(`${uid} in a new plan must read Idle only, never live: ${codes(uid)}`);
  if (badgeHtml(rowForDeedUid('deed-a')).includes('live notice') || badgeHtml(rowForDeedUid('deed-a')).includes('Live Holder')) fail('slide-out shows live detail');
  if (pulse(rowA)) fail('a live staged change must not pulse a planning row');

  // A planned roster: no badge while pending, then the plan answer's own (unpowered, over cap).
  answer = () => ({ powered: false, base_pp_cap_reached: true });
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-b', group_id: 'worker', slot_index: 0, asset_ref: 'w-1', asset: { name: 'Planned' } });
  if (codes('deed-b') !== '') fail(`a pending planned row shows no badge: ${codes('deed-b')}`);
  await wait(400);
  // The answer: 1 of 5 slots, unpowered, at the cap -- Over Cap, 4 open, Unpowered, all from the plan.
  if (codes('deed-b') !== 'over_cap,underfilled,unpowered') fail(`planned badges follow the plan: ${codes('deed-b')}`);
  if (!badgeHtml(rowForDeedUid('deed-b')).includes('4 open')) fail('open slots follow the planned roster');

  // Menu > Live: the live badges exactly as before.
  planOff();
  if (codes('deed-a') !== liveCodesA || codes('deed-b') !== liveCodesB || badgeStripHtml(rowForDeedUid('deed-a')) !== liveStripA) fail('Live badges changed');
  if (!pulse(rowA)) fail('Live pulse lost');
  if (rowA.badges.join(',') !== 'unpowered,underfilled') fail('the live data was mutated');
""")


def test_power_warning_in_the_open_plot_reads_the_plan_never_live():
    _run(r"""
  // Live: a worker staked, no power source -- the band warns. A second plot has a live Power Core and no warning.
  const facts = { is_energized: false, is_power_core_staked: false, is_runi_staked: false };
  const rowA = liveRow('deed-a', '1-1-1', 4000, { staking_facts: facts, assets_loaded: true,
    workers: [{ slot: 1, asset_ref: 'live-w', name: 'Live Worker', card_name: 'Live Worker', base_pp: 100 }] });
  const rowB = liveRow('deed-b', '1-1-2', 3000, { staking_facts: { ...facts, is_power_core_staked: true }, assets_loaded: true,
    workers: [{ slot: 1, asset_ref: 'live-w2', name: 'Live Worker 2', card_name: 'Live Worker 2', base_pp: 100 }] });
  BOARD = { player: 'player0013', generated_at: '2026-10-30T10:00:00Z', plots: [rowA, rowB], notices: [], totals: {} };
  const warns = (row) => renderSlotEditor(row).includes('data-plot-warning-kind="power"');
  const liveA = renderSlotEditor(rowA);
  if (!warns(rowA) || warns(rowB)) fail('live fixture: A warns, B (Power Core) does not');

  // A new plan: nothing staked, no warning -- never the live roster's warning.
  planOn();
  boardPlanningStore();
  if (warns(rowA)) fail('an empty plan must not show the live power warning');

  // B planned with a worker: no warning while pending (no plan value), then the plan's answer decides.
  answer = () => ({ powered: false });
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-b', group_id: 'worker', slot_index: 0, asset_ref: 'w-1', asset: { name: 'Planned' } });
  if (warns(rowB)) fail('a pending planned roster has no power value yet: no warning');
  renderSlotEditor(rowB);
  await wait(400);
  if (!warns(rowB)) fail('the plan says unpowered: the band must warn even though Live has a Power Core');
  answer = () => ({ powered: true });
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-a', group_id: 'worker', slot_index: 0, asset_ref: 'w-2', asset: { name: 'Planned 2' } });
  renderSlotEditor(rowA);
  await wait(400);
  if (warns(rowA)) fail('the plan says powered: no warning even though Live is unpowered');

  // Menu > Live: the band exactly as before.
  planOff();
  if (renderSlotEditor(rowA) !== liveA) fail('Live band changed');
""")


def test_filters_read_the_plan_projection_never_the_live_row():
    _run(r"""
  const els = {};
  const sel = (values) => ({ value: values[0] || '', selectedOptions: values.map((value) => ({ value })), options: [] });
  const setFilters = (f) => {
    for (const id of ['region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter', 'status-filter', 'badge-filter']) els[id] = sel(f[id] || []);
    els['text-filter'] = { value: f.text || '' };
    els['staged-only'] = { checked: !!f.staged };
  };
  document.getElementById = (id) => els[id] || null;
  const rowA = liveRow('deed-a', '1-1-1', 4000, { badges: ['unpowered'], worker_names: ['Live Worker'] });
  const rowB = liveRow('deed-b', '1-1-2', 3000, { badges: [], worker_names: ['Other Live'] });
  BOARD = { player: 'player0013', generated_at: '2026-10-30T10:00:00Z', plots: [rowA, rowB], notices: [], totals: {} };
  CERES_STAGE.push({ deed_uid: 'deed-a', group_id: 'worker', action: 'stake', asset_ref: 'staged-1', slot_index: 1 });
  const pick = (f) => { setFilters(f); return BOARD.plots.filter((row) => matchesFilters(row)).map((row) => row.deed_uid).join(','); };
  const live = [pick({ 'badge-filter': ['unpowered'] }), pick({ 'badge-filter': ['idle'] }), pick({ text: 'live worker' }), pick({ staged: true })];
  if (live.join('|') !== 'deed-a||deed-a|deed-a') fail(`live fixture: ${live.join('|')}`);

  planOn();
  boardPlanningStore();
  // A new plan: every plot is Idle, no live badge, no live worker name, no live staged change.
  if (pick({ 'badge-filter': ['unpowered'] }) !== '') fail('a live badge must not filter in planning');
  if (pick({ 'badge-filter': ['idle'] }) !== 'deed-a,deed-b') fail(`Idle follows the plan: ${pick({ 'badge-filter': ['idle'] })}`);
  if (pick({ text: 'live worker' }) !== '') fail('a live worker name must not match in planning');
  if (pick({ staged: true }) !== '') fail('a live staged change is not a plan change');
  // A planned worker on B: found by its planned name, counted as changed, Unpowered once the plan says so.
  answer = () => ({ powered: false });
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-b', group_id: 'worker', slot_index: 0, asset_ref: 'w-1', asset: { name: 'Planned Ox' } });
  pick({});
  await wait(400);
  if (pick({ text: 'planned ox' }) !== 'deed-b') fail('the planned worker name finds its plot');
  if (pick({ staged: true }) !== 'deed-b') fail('"changed only" shows the plots the plan changes');
  if (pick({ 'badge-filter': ['unpowered'] }) !== 'deed-b') fail('the badge filter follows the plan answer');

  planOff();
  const back = [pick({ 'badge-filter': ['unpowered'] }), pick({ 'badge-filter': ['idle'] }), pick({ text: 'live worker' }), pick({ staged: true })];
  if (back.join('|') !== live.join('|')) fail(`Live filters changed: ${back.join('|')}`);
""")


def test_open_plot_footer_has_no_live_harvest_and_affinity_is_a_plot_fact():
    _run(r"""
  const facts = { is_energized: false, is_power_core_staked: true, is_runi_staked: false };
  const harvest = { state: 'harvesting', token: 'GRAIN', net_at_anchor: 123.45, anchor_at: '2026-10-30T00:00:00Z',
    hours_at_anchor: 1, gross_per_hour: 0, tax_rate: 0.1 };
  const biome = { fire: 0.1, water: -0.1 };
  const rowA = liveRow('deed-a', '1-1-1', 4000, { staking_facts: facts, assets_loaded: true, harvest, biome,
    workers: [{ slot: 1, asset_ref: 'live-w', name: 'Live Worker', card_name: 'Live Worker', base_pp: 100 }] });
  BOARD = { player: 'player0013', generated_at: '2026-10-30T10:00:00Z', plots: [rowA], notices: [], totals: {} };
  const footer = (html) => html.slice(html.indexOf('<footer class="slot-editor-footer"'), html.indexOf('</footer>'));
  const affinity = (html) => (html.match(/<span class="terrain-affinity"[^>]*>.*?<\/span><\/span>|data-terrain-affinity="[^"]*"[^>]*>/g) || []).join('');
  const liveBand = renderSlotEditor(rowA);
  if (!footer(liveBand).includes('123.45')) fail(`live fixture shows its accrued harvest: ${footer(liveBand)}`);

  planOn();
  boardPlanningStore();
  const planBand = renderSlotEditor(rowA);
  const planFooter = footer(planBand);
  if (planFooter.includes('123.45') || planFooter.includes('You receive') || planFooter.includes('data-harvest-action')) fail(`planning footer shows live harvest: ${planFooter}`);
  if (!planFooter.includes('<span class="harvest-bar-state">—</span>')) fail(`planning footer must read the board's dash: ${planFooter}`);
  // Terrain affinity is the plot's terrain modifier per splinter (row.biome), not the staked cards: the same
  // line in planning as in Live, and no live card reaches it.
  if (!affinity(planBand) || affinity(planBand) !== affinity(liveBand)) fail('terrain affinity is the plot fact, same in planning');
  if (affinity(planBand).includes('Live Worker')) fail('no live card in the affinity line');

  planOff();
  if (renderSlotEditor(rowA) !== liveBand) fail('Live band changed');
""")


def test_plot_fact_badges_stay_including_the_deeds_unstable_totem():
    _run(r"""
  const rowA = liveRow('deed-a', '1-1-1', 4000, { badges: ['unstable_totem', 'building_in_box', 'worksite_incomplete', 'unpowered'],
    notices: [{ code: 'building_in_box', message: 'BiB note' }, { code: 'unstable_totem', message: 'UT note' }, { code: 'unpowered', message: 'live power note' }] });
  const rowB = liveRow('deed-b', '1-1-2', 3000, { badges: ['no_worksite', 'unstable_totem'] });
  BOARD = { player: 'player0013', generated_at: '2026-10-30T10:00:00Z', plots: [rowA, rowB], notices: [], totals: {} };
  const codes = (uid) => badgeCodesFor(rowForDeedUid(uid)).slice().sort().join(',');
  const liveA = codes('deed-a');
  planOn();
  boardPlanningStore();
  // A new plan: the plot facts stay (with their own notice), incl. the deed's Unstable Totem; staking badges do not.
  if (codes('deed-a') !== 'building_in_box,idle,unstable_totem,worksite_incomplete') fail(`plot facts in a new plan: ${codes('deed-a')}`);
  if (codes('deed-b') !== 'idle,no_worksite,unstable_totem') fail(`plot facts in a new plan: ${codes('deed-b')}`);
  const html = badgeHtml(rowForDeedUid('deed-a'));
  if (!html.includes('BiB note') || !html.includes('UT note') || html.includes('live power note')) fail('a plot fact keeps its own notice, nothing else live');
  // A planned totem that is not marked unstable: no UT; plot facts stay even while the answer is pending.
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-a', group_id: 'totem', slot_index: 0, asset_ref: 't-1', asset: { name: 'Plain Totem' } });
  if (codes('deed-a') !== 'building_in_box,unstable_totem,worksite_incomplete') fail(`pending planned row keeps plot facts only: ${codes('deed-a')}`);
  // A planned totem does not change the deed's Unstable Totem: the plot facts stay on a planned row.
  await boardPlanningStore().setPlacement({ deed_uid: 'deed-b', group_id: 'totem', slot_index: 0, asset_ref: 't-2', asset: { name: 'Plain Totem' } });
  await wait(400);
  if (!codes('deed-b').includes('unstable_totem') || !codes('deed-b').includes('no_worksite')) fail(`plot facts stay on a planned row: ${codes('deed-b')}`);
  planOff();
  if (codes('deed-a') !== liveA) fail('Live badges changed');
""")
