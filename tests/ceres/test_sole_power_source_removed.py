from __future__ import annotations

from ceres.op_preview import sole_power_source_removed, workers_staged_without_power


def _plot(**staking_facts_overrides) -> dict:
    staking_facts = {
        "worker_count": 1,
        "total_harvest_pp": 1000,
        "is_power_core_staked": False,
        "is_energized": False,
        "is_runi_staked": False,
    }
    staking_facts.update(staking_facts_overrides)
    return {"staking_facts": staking_facts}


def test_removing_a_plots_sole_runi_raises_the_hazard():
    """Per the internal rules's measured rule, `is_powered = power_core OR energized OR runi` with
 0 exceptions across 6,903 plots -- a staked Runi is independently sufficient to power a
 plot, exactly like a Power Core. Staging its removal (runi_delta -1) with nothing else
 powering the plot must raise the hazard, mirroring the existing Power Core case. This
 exercises `sole_power_source_removed()` directly rather than through `build_preview()`,
 because the staging path currently refuses all Runi moves outright (`STK-LND-RUNI` is
 UNVERIFIED) -- the hazard math must be correct now regardless."""
    plot = _plot(is_runi_staked=True)
    assert sole_power_source_removed(plot, "deed-1", core_delta={}, runi_delta={"deed-1": -1}) is True


def test_runi_removal_is_not_a_hazard_when_a_power_core_also_covers_the_plot():
    """Power Core and Runi are independently sufficient power sources -- losing one while
 the other remains must NOT raise the hazard."""
    plot = _plot(is_runi_staked=True, is_power_core_staked=True)
    assert sole_power_source_removed(plot, "deed-1", core_delta={}, runi_delta={"deed-1": -1}) is False


def test_runi_removal_is_not_a_hazard_when_energized_also_covers_the_plot():
    plot = _plot(is_runi_staked=True, is_energized=True)
    assert sole_power_source_removed(plot, "deed-1", core_delta={}, runi_delta={"deed-1": -1}) is False


def test_runi_removal_is_not_a_hazard_when_the_plot_has_no_workers_or_pp():
    """The warning is suppressed when there is nothing left to protect -- a plot with
 zero seated workers and zero harvest PP loses no capacity and stops no production,
 so losing its sole Runi is not flagged."""
    plot = _plot(is_runi_staked=True, worker_count=0, total_harvest_pp=0)
    assert sole_power_source_removed(plot, "deed-1", core_delta={}, runi_delta={"deed-1": -1}) is False


def test_staging_a_runi_onto_an_unpowered_plot_resolves_no_hazard():
    plot = _plot()
    assert sole_power_source_removed(plot, "deed-1", core_delta={}, runi_delta={"deed-1": 1}) is False


def test_dec_never_substitutes_for_a_power_source():
    """DEC staking does NOT substitute for a power source (the internal rules) -- a plot with no
 Power Core, no ENERGIZED, and no Runi is unpowered regardless of any DEC field, so the
 hazard still fires on its sole Runi's removal even if `dec_staked` (or similar) is
 present in `staking_facts`."""
    plot = _plot(is_runi_staked=True, dec_staked=50000)
    assert sole_power_source_removed(plot, "deed-1", core_delta={}, runi_delta={"deed-1": -1}) is True


def test_staging_a_worker_while_unstaking_the_sole_runi_raises_both_hazards():
    """A batch staging a worker onto a plot while unstaking that plot's sole Runi (no
 Power Core, not ENERGIZED, workers seated) must raise BOTH `sole_power_source_removed`
 and `workers_staged_without_power` -- the plot loses its only power source at the same
 moment it gains a worker that needs one. `workers_staged_without_power()` must read the
 Runi's removal via `runi_delta`, exactly mirroring how it already reads a Power Core's
 removal via `core_delta`, rather than the raw (pre-batch) `is_runi_staked` snapshot flag."""
    plot = _plot(is_runi_staked=True, worker_count=4, total_harvest_pp=1000)
    runi_delta = {"deed-1": -1}
    core_delta: dict[str, int] = {}
    assert sole_power_source_removed(plot, "deed-1", core_delta, runi_delta) is True
    assert workers_staged_without_power(plot, "deed-1", current_workers=4, worker_delta=1,
                                         core_delta=core_delta, runi_delta=runi_delta) is True


def test_staging_a_worker_while_runi_remains_does_not_claim_the_plot_has_no_power():
    plot = _plot(is_runi_staked=True, worker_count=4, total_harvest_pp=1000)
    assert workers_staged_without_power(
        plot,
        "deed-1",
        current_workers=4,
        worker_delta=1,
        core_delta={},
        runi_delta={},
    ) is False


def test_staging_a_worker_while_unstaking_the_sole_power_core_raises_both_hazards():
    """The equivalent Power Core batch must raise the same two hazards -- this is the case
 the pre-fix code already handled correctly, kept here so both power sources are
 covered by the same shape of test."""
    plot = _plot(is_power_core_staked=True, worker_count=4, total_harvest_pp=1000)
    core_delta = {"deed-1": -1}
    runi_delta: dict[str, int] = {}
    assert sole_power_source_removed(plot, "deed-1", core_delta, runi_delta) is True
    assert workers_staged_without_power(plot, "deed-1", current_workers=4, worker_delta=1,
                                         core_delta=core_delta, runi_delta=runi_delta) is True
