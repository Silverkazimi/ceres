from ceres.capacity import (
    WORKSITE_WORKER_CAPACITY,
    potential_worker_capacity,
    powered_worker_capacity,
    powered_worker_slots,
    structure_capacity_index,
)


def _worksite(*, powered=0, core=False, energized=False, runi=False):
    return {
        "facts": {"worksite_type": "Grain Farm"},
        "worksite_facts": {"project_type": "worksite_grain"},
        "staking_facts": {
            "max_workers_allowed": powered,
            "is_power_core_staked": core,
            "is_energized": energized,
            "is_runi_staked": runi,
        },
    }


def test_worksite_always_has_five_regular_slots_independent_of_power():
    for plot in (
        _worksite(powered=0),
        _worksite(powered=4, runi=True),
        _worksite(powered=5, core=True),
        _worksite(powered=5, energized=True),
    ):
        index = structure_capacity_index({"plots": [plot]})
        assert potential_worker_capacity(plot, index) == WORKSITE_WORKER_CAPACITY
        assert powered_worker_capacity(plot, index) == WORKSITE_WORKER_CAPACITY


def test_worksite_powered_slots_are_separate_from_five_slot_capacity():
    cases = (
        (_worksite(powered=0), 0),
        (_worksite(powered=4, runi=True), 4),
        (_worksite(powered=5, core=True), 5),
        (_worksite(powered=5, energized=True), 5),
    )
    for plot, expected in cases:
        assert powered_worker_slots(plot, structure_capacity_index({"plots": [plot]})) == expected


def test_projected_power_flags_do_not_change_worksite_slot_count():
    plot = _worksite(powered=4, runi=True)
    index = structure_capacity_index({"plots": [plot]})

    assert powered_worker_slots(
        plot,
        index,
        flags={**plot["staking_facts"], "is_power_core_staked": True},
    ) == 5
    assert powered_worker_slots(
        plot,
        index,
        flags={**plot["staking_facts"], "is_runi_staked": False},
    ) == 0
    assert potential_worker_capacity(plot, index) == 5
