"""an uncleared plot (is_construction with no worksite and no start) is `no_project`, never
`construction`; a real build (start date / target) keeps its construction facts."""
from __future__ import annotations

from ceres.harvest import project_harvest_plot


def _plot(worksite_facts: dict | None) -> dict:
    return {
        "deed_uid": "I-290-test",
        "facts": {"plot": "41-6-44", "is_construction": True, "tax_rate": 0.1, "region": "PR-BRL-41"},
        "staking_facts": {},
        "worksite_facts": worksite_facts or {},
    }


def test_uncleared_plot_is_no_project() -> None:
    out = project_harvest_plot(_plot(None))
    assert out["state"] == "no_project"
    assert "construction" not in out


def test_uncleared_plot_fresh_empty_override_is_no_project() -> None:
    out = project_harvest_plot(_plot(None), {})
    assert out["state"] == "no_project"


def test_real_build_keeps_construction() -> None:
    wf = {"project_type": "worksite_sps_construction", "start_date": "2026-09-13T21:27:30.000Z", "projected_end": "2026-09-14T21:27:30.000Z"}
    out = project_harvest_plot(_plot(wf))
    assert out["state"] == "construction"
    assert out["construction"]["started_at"] == "2026-09-13T21:27:30.000Z"
    assert out["construction"]["to_label"] == "Shard Mine"


def test_build_with_start_but_no_end_stays_construction() -> None:
    wf = {"project_type": "worksite_grain_construction", "start_date": "2026-09-13T21:27:30.000Z"}
    out = project_harvest_plot(_plot(wf))
    assert out["state"] == "construction"
    assert out["construction"]["ready_at"] is None


# a finished build is completed by feeding workers Grain, never by reloading the board
# the construction state carries the same live worksite_completion facts ceres/board.py computes
# at page load, so a per-plot fresh read (ceres/routes_harvest.py) also picks up completion the
# moment it happens, even if the board loaded before the countdown ended.
def _plot_with_staking(worksite_facts: dict, *, total_work_per_hour: float = 100.0) -> dict:
    plot = _plot(worksite_facts)
    plot["staking_facts"] = {"total_work_per_hour": total_work_per_hour}
    return plot


def test_construction_not_yet_ready_has_no_completion() -> None:
    wf = {
        "project_type": "worksite_stone_construction", "start_date": "2026-09-13T21:27:30.000Z",
        "projected_end": "2099-01-01T00:00:00Z", "hours_to_completion": 5, "grain_required": 500,
    }
    out = project_harvest_plot(_plot_with_staking(wf))
    assert out["state"] == "construction"
    assert out["worksite_completion"] is None


def test_construction_ready_carries_worksite_completion() -> None:
    wf = {
        "project_type": "worksite_stone_construction", "start_date": "2020-01-01T00:00:00Z",
        "projected_end": "2020-01-02T00:00:00Z", "hours_to_completion": 0, "grain_required": 500,
        "project_id": 397948,
    }
    out = project_harvest_plot(_plot_with_staking(wf))
    assert out["state"] == "construction"
    completion = out["worksite_completion"]
    assert completion is not None
    assert completion["to"] == "stone"
    assert completion["grain_cost"] == 500
    assert completion["project_id"] == 397948


def test_construction_ready_but_zero_work_per_hour_has_no_completion() -> None:
    # H1-style guard (worksite_swap.ready_worksite_change): a plot that could never fund/finish the
    # change (0 work_per_hour) is not reported as ready, matching ceres/board.py's own rule.
    wf = {
        "project_type": "worksite_stone_construction", "start_date": "2020-01-01T00:00:00Z",
        "projected_end": "2020-01-02T00:00:00Z", "hours_to_completion": 0, "grain_required": 500,
    }
    out = project_harvest_plot(_plot_with_staking(wf, total_work_per_hour=0))
    assert out["state"] == "construction"
    assert out["worksite_completion"] is None
