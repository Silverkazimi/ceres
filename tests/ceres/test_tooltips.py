"""Static Node/HTML counterfactuals for the plot tooltip bundle."""
from __future__ import annotations

from .vm_context import run_in_module_context


def test_rarity_bonus_comes_from_each_row_api_value() -> None:
    run_in_module_context(r"""
const common = plotRarityCellHtml({ rarity: 'common', rarity_production_bonus: 0 });
const legendary = plotRarityCellHtml({ rarity: 'legendary', rarity_production_bonus: 1 });
if (!/Production bonus[^]*\+0%/.test(common)) throw new Error('common API bonus missing');
if (!/Production bonus[^]*\+100%/.test(legendary)) throw new Error('legendary API bonus missing');
if (common === legendary) throw new Error('rarity bonus ignored the row counterfactual');
""")


def test_magical_icons_share_the_title_style_single_trigger() -> None:
    run_in_module_context(r"""
const row = {
  plot: '1-1-1', deed_uid: 'magic-tip', territory: 'Praetoria', terrain: 'Plains', resource: 'Grain',
  worksite_type: 'Farm', plot_status: 'magical', magic_type: 'fire', rarity: 'rare', rarity_production_bonus: 0.25,
  harvest_pp: 300, construction_pp: 200, base_pp: 100, base_pp_after_cap: 100, base_pp_cap: 100000,
  pp_tooltip: { workers: ['Fire Worker'], terrain_boost: 1, production_boost: 0.5, production_modifiers: { deed_rarity_boost: 0.25, title_boost: 0.25 } },
  multiplier: 1, work_per_hour: 2, food_per_hour: 0, input_costs_per_hour: {}, capacity: { display: '1' }, dec: { needed: 0 }, bonus: { totem: null, title: null },
};
const typeCell = plotRowCellsHtml(row).match(/<td class="type-cell"[^]*?<\/td>/)?.[0] || '';
if ((typeCell.match(/\bdata-tip=/g) || []).length !== 1) throw new Error(`magical type must own one trigger: ${typeCell}`);
if (/<img\b[^>]*\bdata-tip=/.test(typeCell)) throw new Error('magical child icon retained its own trigger');
if (!/status-magical-off\.svg/.test(typeCell) || !/splinter\/fire\.svg/.test(typeCell)) throw new Error('magical double icon missing');
const natural = plotRowCellsHtml({ ...row, plot_status: 'natural', magic_type: null });
if (natural.includes('splinter/fire.svg')) throw new Error('magical element survived the counterfactual');
""")


def test_base_cap_detail_only_when_cap_is_reached() -> None:
    run_in_module_context(r"""
const below = basePpTipAttrs({ base_pp: 99, base_pp_after_cap: 99, base_pp_cap: 100, base_pp_cap_reached: false });
const reached = basePpTipAttrs({ base_pp: 100, base_pp_after_cap: 100, base_pp_cap: 100, base_pp_cap_reached: true });
const over = basePpTipAttrs({ base_pp: 130, base_pp_after_cap: 100, base_pp_cap: 100, base_pp_cap_reached: true });
if (/Before cap|cap\./.test(below)) throw new Error('below-cap row exposed cap detail');
if (!/at its 100 cap/.test(reached)) throw new Error('at-cap row lacks cap detail');
if (!/removes 30 PP/.test(over)) throw new Error('over-cap row lacks actual loss');
""")


def test_boostable_and_total_use_plot_specific_workers_and_modifiers() -> None:
    run_in_module_context(r"""
const row = {
  base_pp: 100, base_pp_after_cap: 100, construction_pp: 125, harvest_pp: 200,
  pp_tooltip: { workers: ['Alpha', 'Beta'], terrain_boost: 0.25, production_boost: 0.6, production_modifiers: { deed_rarity_boost: 0.1, title_boost: 0.5 } },
};
const changed = { ...row, construction_pp: 80, harvest_pp: 88, pp_tooltip: { ...row.pp_tooltip, workers: ['Gamma'], terrain_boost: -0.2, production_boost: 0.1, production_modifiers: { deed_rarity_boost: 0.1 } } };
const boostable = boostablePpTipAttrs(row);
const total = totalPpTipAttrs(row);
const changedBoostable = boostablePpTipAttrs(changed);
const changedTotal = totalPpTipAttrs(changed);
if (!/Alpha, Beta/.test(boostable) || !/Terrain modifier: \+25%/.test(boostable) || !/Boostable PP: 125/.test(boostable)) throw new Error('boostable detail missing');
if (!/Title: \+50%/.test(total) || !/Production modifiers total: \+60%/.test(total) || !/Total PP: 200/.test(total)) throw new Error('total detail missing');
if (/Alpha, Beta/.test(changedBoostable) || !/Gamma/.test(changedBoostable) || /Total PP: 200/.test(changedTotal) || !/Total PP: 88/.test(changedTotal)) throw new Error('PP tooltip ignored the row counterfactual');
""")
