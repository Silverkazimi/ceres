""" hourly_resource_costs() (Wood/Stone/Iron per hour) and its two call sites,
ceres/harvest.py::project_harvest_plot and ceres/board.py::_plot_row, sharing one formula
(qty * base_pp per recipe entry). On an ordinary one-entry-per-symbol recipe the two surfaces
agree exactly, value for value ( keeps this true after the duplicate-symbol fix below).
On a recipe with two entries of the same symbol they agree in SUM, not per-row: board.py's
own `wood_cost_per_hour`/etc. read hourly_resource_costs()'s per-symbol total, while
project_harvest_plot's `costs` list keeps one row per recipe entry in that entry's own
value -- see test_duplicate_symbol_recipe_entries_keep_their_own_per_entry_value below.
"""

from __future__ import annotations

from ceres.harvest import hourly_resource_costs, project_harvest_plot


def _recipe(*entries):
    return [{"symbol": s, "qty": q} for s, q in entries]


def test_recipe_times_effective_base_pp():
    wf = {"resource_recipe": _recipe(("WOOD", 0.005), ("STONE", 0.002), ("IRON", 0.0005))}
    sf = {"total_base_pp_after_cap": 40000.0}
    assert hourly_resource_costs(wf, sf) == {"WOOD": 200.0, "STONE": 80.0, "IRON": 20.0}


def test_no_recipe_is_none():
    assert hourly_resource_costs({}, {"total_base_pp_after_cap": 40000.0}) is None
    assert hourly_resource_costs({"resource_recipe": []}, {"total_base_pp_after_cap": 40000.0}) is None
    assert hourly_resource_costs(None, {"total_base_pp_after_cap": 40000.0}) is None
    # A recipe with no material (WOOD/STONE/IRON) entries -- e.g. a base-resource worksite
    # is the same as no recipe: nothing to report, never a zero.
    assert hourly_resource_costs({"resource_recipe": _recipe(("GRAIN", 0.01))}, {"total_base_pp_after_cap": 40000.0}) is None


def test_zero_effective_pp_reports_measured_zeros_not_none():
    wf = {"resource_recipe": _recipe(("WOOD", 0.005), ("IRON", 0.0005))}
    sf = {"total_base_pp_after_cap": 0.0}
    assert hourly_resource_costs(wf, sf) == {"WOOD": 0.0, "IRON": 0.0}


def test_malformed_recipe_or_missing_facts_is_none():
    assert hourly_resource_costs({"resource_recipe": "not-a-list"}, {"total_base_pp_after_cap": 40000.0}) is None
    assert hourly_resource_costs({"resource_recipe": _recipe(("WOOD", 0.005))}, {}) is None
    assert hourly_resource_costs({"resource_recipe": _recipe(("WOOD", 0.005))}, {"total_base_pp_after_cap": None}) is None
    # An entry with a bad qty is dropped, not fatal to the whole recipe.
    wf = {"resource_recipe": [{"symbol": "WOOD", "qty": "not-a-number"}, {"symbol": "IRON", "qty": 0.0005}]}
    assert hourly_resource_costs(wf, {"total_base_pp_after_cap": 1000.0}) == {"IRON": 0.5}


def test_qty_zero_recipe_entry_is_a_measured_zero_not_dropped():
    # parity finding: project_harvest_plot's own recipe loop keeps a qty-0 entry
    # (only `qty is None` drops one there) -- hourly_resource_costs must match, or the two
    # surfaces disagree on exactly this plot. A genuine qty-0 recipe entry IS a real recipe
    # (unlike an empty/missing one), so this reports {"WOOD": 0.0}, not None.
    wf_zero_qty = {"resource_recipe": _recipe(("WOOD", 0.0))}
    assert hourly_resource_costs(wf_zero_qty, {"total_base_pp_after_cap": 1000.0}) == {"WOOD": 0.0}
    wf_mixed = {"resource_recipe": _recipe(("WOOD", 0.0), ("IRON", 0.0005))}
    assert hourly_resource_costs(wf_mixed, {"total_base_pp_after_cap": 1000.0}) == {"WOOD": 0.0, "IRON": 0.5}


def _sps_plot(resource_recipe, base_pp_after_cap):
    worksite = {
        "is_active": True, "token_symbol": "SPS", "is_harvesting": True,
        "last_action_time": "2026-09-01T00:00:00Z", "hours_since_last_op": 1.0,
        "rewards_per_hour": 10.0, "projected_amount_received": 10.0,
        "grain_req_per_hour": 5.0, "resource_recipe": resource_recipe,
    }
    return {
        "deed_uid": "1-1-1",
        "facts": {"plot": "1-1-1", "resource": "SPS"},
        "staking_facts": {"total_base_pp_after_cap": base_pp_after_cap},
        "worksite_facts": worksite,
    }


def test_board_and_harvest_call_sites_agree_on_per_hour_costs():
    recipe = _recipe(("WOOD", 0.005), ("STONE", 0.002), ("IRON", 0.0005))
    plot = _sps_plot(recipe, 40000.0)
    wf, sf = plot["worksite_facts"], plot["staking_facts"]

    # ceres/board.py::_plot_row calls hourly_resource_costs(wf, sf) directly.
    board_costs = hourly_resource_costs(wf, sf)

    # ceres/harvest.py::project_harvest_plot derives its own `costs` list directly (qty *
    # base_pp per recipe entry -- ) -- on a one-entry-per-symbol recipe this equals
    # the shared helper's per-symbol reading, so the two surfaces still agree here.
    projection = project_harvest_plot(plot)
    harvest_costs = {c["symbol"]: c["per_hour"] for c in projection["costs"] if c["symbol"] in ("WOOD", "STONE", "IRON")}

    assert board_costs == {"WOOD": 200.0, "STONE": 80.0, "IRON": 20.0}
    assert harvest_costs == board_costs


def test_board_and_harvest_call_sites_agree_on_a_qty_zero_recipe_entry():
    # parity finding, the exact case that first diverged: a qty-0 material entry.
    recipe = _recipe(("WOOD", 0.0), ("IRON", 0.0005))
    plot = _sps_plot(recipe, 1000.0)
    wf, sf = plot["worksite_facts"], plot["staking_facts"]
    board_costs = hourly_resource_costs(wf, sf)
    projection = project_harvest_plot(plot)
    harvest_costs = {c["symbol"]: c["per_hour"] for c in projection["costs"] if c["symbol"] in ("WOOD", "STONE", "IRON")}
    assert board_costs == {"WOOD": 0.0, "IRON": 0.5}
    assert harvest_costs == board_costs


def test_duplicate_symbol_recipe_entries_keep_their_own_per_entry_value():
    """a recipe with TWO entries of the same symbol used to have project_harvest_plot
 read `hourly_resource_costs()`'s per-SYMBOL SUM for every row of that symbol, so both
 entries reported the combined total instead of their own share. `hourly_resource_costs()`
 (board.py's own reading) legitimately sums per symbol -- that's its contract -- but
 project_harvest_plot's `costs` list keeps one row PER RECIPE ENTRY, in that entry's own
 qty * base_pp. The two surfaces are still consistent: the board's per-symbol total equals
 the SUM of that symbol's individual harvest rows, never each row equalling the total."""
    recipe = _recipe(("WOOD", 0.005), ("WOOD", 0.003), ("IRON", 0.0005))
    plot = _sps_plot(recipe, 40000.0)
    wf, sf = plot["worksite_facts"], plot["staking_facts"]

    board_costs = hourly_resource_costs(wf, sf)
    assert board_costs == {"WOOD": 320.0, "IRON": 20.0}

    projection = project_harvest_plot(plot)
    wood_rows = [c["per_hour"] for c in projection["costs"] if c["symbol"] == "WOOD"]
    iron_rows = [c["per_hour"] for c in projection["costs"] if c["symbol"] == "IRON"]

    # Each entry keeps its OWN qty * base_pp -- never the summed 320.0 on both rows.
    assert wood_rows == [200.0, 120.0]
    assert iron_rows == [20.0]
    # Parity: the board's per-symbol total is exactly the sum of that symbol's harvest rows.
    assert sum(wood_rows) == board_costs["WOOD"]
    assert sum(iron_rows) == board_costs["IRON"]
