""" (Land 2.0 readiness, Phase 0): `ore_split_expectation` against the  plan §B.4 vectors.

Source table: whitepaper "Mining Ores" (revision 2026-09-14), announced, not released. Readings  (ramp from
the threshold) and  (ramp from 0, switched on above the threshold); threshold modes "official" (25k/50k)
and "none" (maintainer-reported ).
"""
from __future__ import annotations

import pytest

from praetoria.ore_split import ORES, ore_split_expectation

# plan §B.4: base PP -> (Rhodium , Rhodium , Vanadium , Vanadium as (min, max, mid) percent
# the plan's own two-decimal figures.
B4 = {
    25_000: ((0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)),
    35_625: ((0.42, 1.42, 0.92), (1.07, 3.56, 2.32), (0, 0, 0), (0, 0, 0)),
    50_000: ((1.00, 3.33, 2.17), (1.50, 5.00, 3.25), (0, 0, 0), (0, 0, 0)),
    57_500: ((1.30, 4.33, 2.82), (1.72, 5.75, 3.74), (0.15, 0.45, 0.30), (0.57, 1.72, 1.15)),
    75_000: ((2.00, 6.67, 4.33), (2.25, 7.50, 4.88), (0.50, 1.50, 1.00), (0.75, 2.25, 1.50)),
    100_000: ((3, 10, 6.5), (3, 10, 6.5), (1, 3, 2.0), (1, 3, 2.0)),
}


def _triple(entry):
    return (entry["min"], entry["max"], entry["mid"])


@pytest.mark.parametrize("base_pp", sorted(B4))
def test_rare_ore_vectors_of_the_resource_plan(base_pp):
    rh_r1, rh_r2, va_r1, va_r2 = B4[base_pp]
    r1 = ore_split_expectation(base_pp, "official", "R1")
    r2 = ore_split_expectation(base_pp, "official", "R2")
    # the plan rounds to two decimals (half-up on its side, so allow one unit in the last place)
    assert _triple(r1["RHODIUM"]) == pytest.approx(rh_r1, abs=0.011)
    assert _triple(r2["RHODIUM"]) == pytest.approx(rh_r2, abs=0.011)
    assert _triple(r1["VANADIUM"]) == pytest.approx(va_r1, abs=0.011)
    assert _triple(r2["VANADIUM"]) == pytest.approx(va_r2, abs=0.011)


def test_exact_values_at_one_vector():
    r1 = ore_split_expectation(35_625, "official", "R1")["RHODIUM"]
    assert r1["min"] == pytest.approx(3 * 10_625 / 75_000) and r1["max"] == pytest.approx(10 * 10_625 / 75_000)
    r2 = ore_split_expectation(57_500, "official", "R2")["VANADIUM"]
    assert (r2["min"], r2["max"]) == pytest.approx((0.575, 1.725))


def test_the_baseline_and_the_100k_column_equal_the_published_table():
    for mode in ("official", "none"):
        for reading in ("R1", "R2"):
            low = ore_split_expectation(0, mode, reading)
            top = ore_split_expectation(100_000, mode, reading)
            assert {o: (low[o]["min"], low[o]["max"]) for o in ORES} == {
                "IRON": (70, 90), "TIN": (10, 25), "COPPER": (0, 10), "RHODIUM": (0, 0), "VANADIUM": (0, 0)}
            assert {o: (top[o]["min"], top[o]["max"]) for o in ORES} == {
                "IRON": (50, 70), "TIN": (20, 35), "COPPER": (5, 15), "RHODIUM": (3, 10), "VANADIUM": (1, 3)}


def test_common_ores_do_not_depend_on_reading_or_threshold_mode():
    for pp in (0, 12_345, 50_000, 87_000):
        results = [ore_split_expectation(pp, m, r) for m in ("official", "none") for r in ("R1", "R2")]
        for ore in ("IRON", "TIN", "COPPER"):
            assert all(res[ore] == results[0][ore] for res in results), (pp, ore)
    iron = ore_split_expectation(50_000)["IRON"]
    assert (iron["min"], iron["max"], iron["mid"]) == (60.0, 80.0, 70.0)


def test_no_thresholds_mode_ramps_rare_ores_from_zero_and_both_readings_agree():
    for pp in (1_000, 10_000, 25_000, 49_999, 80_000):
        r1 = ore_split_expectation(pp, "none", "R1")
        r2 = ore_split_expectation(pp, "none", "R2")
        assert r1 == r2
        assert r1["RHODIUM"]["min"] == pytest.approx(3 * pp / 100_000)
        assert r1["VANADIUM"]["max"] == pytest.approx(3 * pp / 100_000)
    assert ore_split_expectation(10_000, "official")["RHODIUM"]["max"] == 0
    assert ore_split_expectation(10_000, "none")["RHODIUM"]["max"] > 0


def test_r2_jumps_at_the_threshold_and_r1_does_not():
    below = ore_split_expectation(50_000, "official", "R2")["VANADIUM"]["mid"]
    above = ore_split_expectation(50_001, "official", "R2")["VANADIUM"]["mid"]
    assert below == 0 and above > 0.99
    assert ore_split_expectation(50_001, "official", "R1")["VANADIUM"]["mid"] < 0.001


def test_shares_are_monotone_in_base_pp():
    for mode in ("official", "none"):
        for reading in ("R1", "R2"):
            prev = None
            for pp in range(0, 100_001, 2_500):
                cur = ore_split_expectation(pp, mode, reading)
                if prev:
                    for ore in ("TIN", "COPPER", "RHODIUM", "VANADIUM"):
                        assert cur[ore]["mid"] >= prev[ore]["mid"] - 1e-12, (mode, reading, pp, ore)
                    assert cur["IRON"]["mid"] <= prev["IRON"]["mid"] + 1e-12
                prev = cur


def test_input_is_clamped_to_the_100k_cap_and_validated():
    assert ore_split_expectation(250_000) == ore_split_expectation(100_000)
    assert ore_split_expectation(-5) == ore_split_expectation(0)
    assert ore_split_expectation("57500") == ore_split_expectation(57_500)
    for bad in (None, "x", float("nan"), True):
        with pytest.raises(ValueError):
            ore_split_expectation(bad)
    with pytest.raises(ValueError):
        ore_split_expectation(1, "removed")
    with pytest.raises(ValueError):
        ore_split_expectation(1, "official", "R3")
