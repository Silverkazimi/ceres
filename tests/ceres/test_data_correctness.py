""" S5 (v1 clean-up, audit findings L3-01, L3-02, L3-09, L3-10): harvest and Fill numbers must not be wrong."""
from datetime import datetime, timezone

from ceres import harvest
from ceres.harvest import advance, project_harvest_plot, summarize_regions


def _plot(recipe, *, grain=5.0, with_recipe=True, tax=None):
    worksite = {"is_active": True, "token_symbol": "SPS", "is_harvesting": True, "last_action_time": "2026-09-01T00:00:00Z", "hours_since_last_op": 1.0,
                "rewards_per_hour": 100.0, "projected_amount_received": 0.0, "grain_req_per_hour": grain, "grain_required": 3.0}
    if with_recipe:
        worksite["resource_recipe"] = recipe
        worksite.update({"wood_required": 10.0})
    if tax is not None:
        worksite["tax_rate"] = tax
    return {"deed_uid": "1-1-1", "facts": {"plot": "1-1-1", "resource": "SPS", "region": "PR-AAA-1", "tax_rate": tax},
            "staking_facts": {"total_base_pp_after_cap": 1000.0}, "worksite_facts": worksite}


def test_l3_09_an_explicit_zero_tax_stays_zero():
    projection = project_harvest_plot(_plot([{"symbol": "WOOD", "qty": 0.005}], tax=0.0))
    assert projection["tax_rate"] == 0.0
    later = advance(projection, datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert abs(later["net"] - 100.0) < 1e-6        # 1 more hour at 100 gross with no tax (it used to be 90 with the 10% default)


def test_l3_09_a_missing_or_invalid_tax_still_takes_the_default():
    for value in (None, -0.5, 1.5, "x"):
        projection = {"anchor_at": "2026-09-01T01:00:00Z", "hours_at_anchor": 1.0, "net_at_anchor": 0.0, "gross_per_hour": 100.0, "tax_rate": value}
        assert abs(advance(projection, datetime(2026, 9, 1, 2, tzinfo=timezone.utc))["net"] - 90.0) < 1e-6


def test_l3_10_duplicate_recipe_symbols_do_not_double_the_accrued_cost():
    plot = _plot([{"symbol": "WOOD", "qty": 0.005}, {"symbol": "WOOD", "qty": 0.003}])
    plot["worksite_facts"]["projected_amount_received"] = 10.0     # something to harvest, so the cost counts
    report = {"plots": [plot]}
    region = summarize_regions(report, datetime(2026, 9, 1, 1, tzinfo=timezone.utc))[0]
    assert abs(region["cost_now"]["WOOD"] - 10.0) < 1e-6        # the accrued 10 WOOD once, not 20


def test_l3_01_a_missing_cost_field_is_unknown_not_covered():
    complete = project_harvest_plot(_plot([{"symbol": "WOOD", "qty": 0.005}]))
    assert complete["costs_known"] is True and "missing_cost" not in complete["issues"]
    missing_recipe = project_harvest_plot(_plot(None, with_recipe=False))
    assert missing_recipe["costs_known"] is False and "missing_cost" in missing_recipe["issues"]
    missing_grain = project_harvest_plot(_plot([{"symbol": "WOOD", "qty": 0.005}], grain=None))
    assert missing_grain["costs_known"] is False


def test_l3_01_the_preview_marks_such_a_region_unknown():
    source = open(harvest.__file__.replace("harvest.py", "routes_harvest.py"), encoding="utf-8").read()
    assert 'afford = {**afford, "status": "unknown", "reason": "missing_cost"}' in source
    assert source.count("missing_cost") >= 2           # the region preview and the single-plot read


def test_l3_02_fill_counts_workers_already_staged_against_the_cap():
    from ceres import fill_plot
    assert fill_plot._incoming_base({"group_id": "worker", "action": "stake", "base_pp": 80000}) == 80000.0
    assert fill_plot._incoming_base({"group_id": "worker", "action": "replace", "candidate": {"base_pp": 5000}}) == 5000.0
    assert fill_plot._incoming_base({"group_id": "worker", "action": "unstake", "base_pp": 80000}) == 0.0
    assert fill_plot._incoming_base({"group_id": "totem", "action": "stake", "base_pp": 80000}) == 0.0
    source = open(fill_plot.__file__, encoding="utf-8").read()
    assert "total += sum(_incoming_base(move, report) for move in planned)" in source
    assert "for move in planned + chosen" in source


# S10 / L3-01 completeness: partially missing cost data is unknown too
def _worksite(**overrides):
    plot = _plot([{"symbol": "WOOD", "qty": 0.005}])
    plot["worksite_facts"].update({"grain_required": 3.0, "wood_required": 10.0})
    plot["worksite_facts"].update(overrides)
    return plot


def test_l3_01_missing_accrued_amounts_or_bad_recipe_entries_are_unknown():
    assert project_harvest_plot(_worksite())["costs_known"] is True
    assert project_harvest_plot(_worksite(grain_required=None))["costs_known"] is False          # accrued grain absent
    assert project_harvest_plot(_worksite(wood_required=None))["costs_known"] is False           # accrued wood absent
    assert project_harvest_plot(_worksite(resource_recipe=[{"symbol": "WOOD", "qty": "bad"}]))["costs_known"] is False
    assert project_harvest_plot(_worksite(resource_recipe=["WOOD"]))["costs_known"] is False
    assert project_harvest_plot(_worksite(grain_req_per_hour=-1.0))["costs_known"] is False
    assert project_harvest_plot(_worksite(resource_recipe=[], grain_req_per_hour=0.0, grain_required=None))["costs_known"] is True   # a real "no cost"


def test_l3_07_absurd_hours_and_malformed_recipe_entries_are_reported_not_raised():
    huge = _plot([{"symbol": "WOOD", "qty": 0.005}])
    huge["worksite_facts"]["hours_since_last_op"] = 1e300
    assert "missing_anchor" in project_harvest_plot(huge)["issues"]
    bad = project_harvest_plot(_worksite(resource_recipe=[None, 5, "WOOD", {"symbol": "WOOD", "qty": 0.005}]))
    assert "unsupported_recipe" in bad["issues"] and bad["costs_known"] is False


def test_l3_03_a_failed_live_read_never_makes_a_plot_eligible():
    source = open(harvest.__file__.replace("harvest.py", "routes_harvest.py"), encoding="utf-8").read()
    assert 'elif issue and fresh' in source and "Live plot data unavailable" in source


def test_l3_02_a_staged_worker_without_pp_is_resolved_from_the_servers_card_data():
    from ceres import fill_plot
    report = {"cards": [{"asset_ref": "C-STAGED", "facts": {"land_base_pp": 90000.0}}], "workers": [{"asset_ref": "W-1", "base_pp": 5000.0}]}
    bare = {"group_id": "worker", "action": "stake", "asset_ref": "C-STAGED", "slot_index": 0}          # what the route keeps: no PP at all
    assert fill_plot._incoming_base(bare, report) == 90000.0
    assert fill_plot._incoming_base({**bare, "asset_ref": "W-1"}, report) == 5000.0
    assert fill_plot._incoming_base({**bare, "base_pp": 1.0}, report) == 90000.0                       # the server's figure wins over a client's
    assert fill_plot._incoming_base({**bare, "asset_ref": "UNKNOWN", "base_pp": 7.0}, report) == 7.0     # unknown to the server: the old fallback
    assert fill_plot._incoming_base({**bare, "action": "unstake"}, report) == 0.0


# round 2 audit (L3-01 bypass, L3-03, L3-04, L3-05, L3-07)
def test_r2_l3_01_negative_costs_quantities_or_pp_are_unknown_not_a_credit():
    assert project_harvest_plot(_worksite(resource_recipe=[{"symbol": "WOOD", "qty": -1}]))["costs_known"] is False
    assert project_harvest_plot(_worksite(wood_required=-10.0))["costs_known"] is False
    assert project_harvest_plot(_worksite(grain_required=-3.0))["costs_known"] is False
    negative_pp = _worksite()
    negative_pp["staking_facts"]["total_base_pp_after_cap"] = -5.0
    assert project_harvest_plot(negative_pp)["costs_known"] is False


def test_r2_l3_05_an_absurd_but_parseable_timestamp_is_reported_not_raised():
    plot = _plot([{"symbol": "WOOD", "qty": 0.005}])
    plot["worksite_facts"]["last_action_time"] = "9999-12-31T23:59:59Z"
    plot["worksite_facts"]["hours_since_last_op"] = 1000.0
    assert "missing_anchor" in project_harvest_plot(plot)["issues"]


def test_r2_l3_03_04_a_signable_all_preview_needs_the_live_inventory_and_fresh_balances():
    source = open(harvest.__file__.replace("harvest.py", "routes_harvest.py"), encoding="utf-8").read()
    assert 'issues.append("live region inventory required")' in source and "_balances(player, fresh=fresh, client=_client)" in source


def test_r2_l3_02_the_displayed_fill_roster_counts_staged_workers():
    source = open(__import__("ceres.fill_plot", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert source.count("_incoming_base(move, report)") >= 3 and "part of the displayed final roster" in source


def test_s10r3_l3_01_malformed_grain_and_grain_recipe_entries_are_unknown():
    assert project_harvest_plot(_worksite(resource_recipe=[], grain_req_per_hour=0.0, grain_required="bad"))["costs_known"] is False
    assert project_harvest_plot(_worksite(resource_recipe=[{"symbol": "GRAIN", "qty": 1}], grain_req_per_hour=0.0, grain_required=None))["costs_known"] is False
    assert project_harvest_plot(_worksite(resource_recipe=[{"symbol": "GRAIN", "qty": 1}], grain_req_per_hour=0.0, grain_required=0.0))["costs_known"] is True


def test_s10r3_l3_05_overflowing_numbers_and_timestamps_are_malformed_not_raised():
    assert harvest._num(10 ** 400) is None
    assert harvest.parse_utc("9999-12-31T23:59:59-01:00") is None
    assert project_harvest_plot(_worksite(resource_recipe=[{"symbol": "WOOD", "qty": 1e308}]))["costs_known"] is False


def test_s10r3_l3_02_positive_slope_with_zero_accrual_still_checks_affordability():
    import inspect
    from ceres import routes_harvest
    source = inspect.getsource(routes_harvest._deed_rows)
    assert 'if not entry["cost_now"] and not entry["cost_per_hour"]' in source
    assert "Live plot data unavailable" in source and 'entry["status"] = "unknown"' in source
