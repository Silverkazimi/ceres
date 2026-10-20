""".

Cause: the bulk board read carries no worker slots (`roster_predates_stake`); on an over-cap plot the
reallocation answers `worker_slot_pending`, `construction_pp` is unknown and the picker draws "—". The plot
open's repair lived in a stored per-plot patch a later capture could drop ( class). : the picker
now reads the plot's slots live for that request when the board read has none -- in memory, nothing stored.
"""
from __future__ import annotations

import copy
import json

import pytest

from ceres import capture as capture_mod
from ceres import routes_candidates as routes_candidates_mod
from ceres.routes_candidates import get_candidates, with_live_slots
from tests.ceres.test_candidates_api import BASIC_DETAILS, _report, _request


def _over_cap_report(slot):
    report = _report(powered=True)
    report["plots"][0]["facts"]["plot_id"] = 101
    report["workers"][0]["slot"] = slot
    report["plots"][0]["staking_facts"]["total_base_pp_cap"] = 500
    report["cards"].append({
        "asset_ref": "idle-base-cap", "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 40001, "land_base_pp": 1000, "level": 1},
    })
    return report


def _live_plot_read(bulk):
    expanded = copy.deepcopy(bulk)
    expanded["workers"][0]["slot"] = 1
    expanded["plots"][0]["asset_fetch"] = {"status": "retrieved"}
    expanded["cards"] = []
    return expanded


ARGS = dict(limit=25, offset=0, slot=None, reserved_asset_ref=[], released_asset_ref=[], bloodline_sub_type=[],
            staged_moves=None, staged_worksite=None, staged_worksite_type=None, q=None, min_pp=None, max_pp=None,
            rarity=None, splinter=None, edition=None, sort="construction_pp", preset=None, include_rented=None)


def _body(response):
    return json.loads(response.body)


def test_picker_boostable_pp_is_priced_from_a_live_slot_read_when_the_board_read_has_no_slots(monkeypatch):
    bulk = _over_cap_report(slot=None)
    before = json.dumps(bulk, sort_keys=True)
    reads = []
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (bulk, object()))
    monkeypatch.setattr(routes_candidates_mod, "load_details_by_id", lambda: BASIC_DETAILS)
    monkeypatch.setattr(capture_mod, "read_plot_live",
                        lambda player, deed_uid, plot_id: reads.append((deed_uid, plot_id)) or _live_plot_read(bulk))
    request = _request()
    request.app.state.ceres_config.stale_after_seconds = 60

    body = _body(get_candidates("plot-api", request, group_id="worker", bucket="idle", **ARGS))

    assert reads == [("plot-api", 101)]
    row = body["candidates"][0]
    assert row["construction_pp"]["status"] == "point"  # never blank
    assert row["construction_pp"]["value"] is not None
    assert json.dumps(bulk, sort_keys=True) == before  # the board read other routes see is untouched


def test_without_the_repair_the_same_plot_is_the_reported_blank(monkeypatch):
    """The regression the maintainer saw: bulk slots, no live read -> worker_slot_pending -> unknown."""
    bulk = _over_cap_report(slot=None)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (bulk, object()))
    monkeypatch.setattr(routes_candidates_mod, "load_details_by_id", lambda: BASIC_DETAILS)
    # conftest: read_plot_live is unanswered in tests -> the report is priced as it is
    body = _body(get_candidates("plot-api", _request(), group_id="worker", bucket="idle", **ARGS))
    assert body["candidates"][0]["construction_pp"]["status"] != "point"


def test_known_slots_cost_no_extra_read(monkeypatch):
    report = _over_cap_report(slot=1)
    monkeypatch.setattr(capture_mod, "read_plot_live",
                        lambda *a: pytest.fail("a plot whose slots are known must not be re-read"))
    assert with_live_slots(object(), "player0005", "plot-api", report) is report


def test_an_unanswered_or_incomplete_live_read_leaves_the_report_as_it_was(monkeypatch):
    report = _over_cap_report(slot=None)
    incomplete = _live_plot_read(report)
    incomplete["plots"][0]["asset_fetch"] = {"status": "failed"}
    monkeypatch.setattr(capture_mod, "read_plot_live", lambda *a: incomplete)
    assert with_live_slots(object(), "player0005", "plot-api", report) is report


def test_the_live_slot_read_stores_nothing():
    """the reader behind read_plot_live refuses any cache read or write."""
    from pathlib import Path

    source = Path(capture_mod.__file__).read_text()
    body = source[source.index("def read_plot_live("):source.index("def read_plots_live(")]
    assert "_NoStore()" in body and "persist_cache = lambda _key: False" in body


def test_removal_and_stage_impact_use_the_same_repair():
    source = __import__("inspect").getsource(routes_candidates_mod)
    assert source.count("report = with_live_slots(") == 3
