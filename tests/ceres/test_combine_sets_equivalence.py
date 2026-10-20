"""Exact output equivalence checks for the multiplicity search."""

from __future__ import annotations

import random

from ceres import card_combinables as cc
from fides_cards.ops import survivor_rule


def _reference_best_combine_set(copies: list[dict], bcx_max: int) -> list[int] | None:
    """Verbatim pre- selector."""
    if not bcx_max or bcx_max <= 0:
        return None
    # state: (cc sum, min(count, 2)) -> chosen indices; count class 2 = "two or more"
    best: dict[tuple[int, int], tuple[int, ...]] = {(0, 0): ()}
    for index, copy in enumerate(copies):
        cc_value = copy["bcx"]
        for (total, klass), chosen in list(best.items()):
            nxt = total + cc_value
            if nxt > bcx_max:
                continue
            key = (nxt, min(klass + 1, 2))
            candidate = chosen + (index,)
            current = best.get(key)
            if current is None or len(candidate) < len(current):
                best[key] = candidate
    pairs = [(total, chosen) for (total, klass), chosen in best.items() if klass == 2]
    if not pairs:
        return None
    total, chosen = max(pairs, key=lambda item: (item[0], -len(item[1])))
    return list(chosen)


def _reference_combine_sets(free: list[dict], bcx_max: int, thresholds: list[int] | None,
                            max_pp: float | None) -> tuple[list[dict], list[dict]]:
    """Verbatim pre- set packer."""
    pool = sorted(free, key=lambda c: (-c["bcx"], str(c["uid"])))
    sets: list[dict] = []
    while len(pool) >= 2 and len(sets) < cc.MAX_SETS_PER_PRINT:
        chosen = _reference_best_combine_set(pool, bcx_max)
        if not chosen:
            break
        parts = [pool[i] for i in chosen]
        survivor = survivor_rule([{"uid": p["uid"], "card_detail_id": p["card_detail_id"], "edition": p["edition"],
                                   "foil": p["foil"], "bcx": p["bcx"]} for p in parts])
        lead = next((p for p in parts if p["uid"] == survivor), parts[0])
        total = sum(p["bcx"] for p in parts)
        sets.append({
            "warnings": [cc.LOCK_WARNING] if any(p.get("warnings") for p in parts) else [],
            "uids": [p["uid"] for p in parts],
            "survivor_uid": None if survivor == "unknown" else survivor,
            "count": len(parts), "destroyed_count": len(parts) - 1,
            "parts_cc": [p["bcx"] for p in parts],
            "before": cc._state(lead["bcx"], thresholds, max_pp, bcx_max),
            "after": cc._state(total, thresholds, max_pp, bcx_max),
            "levels_gained": max(0, (cc.level_for_bcx(thresholds, total) or 0) -
                                 (cc.level_for_bcx(thresholds, lead["bcx"]) or 0)) if thresholds else None,
        })
        taken = set(chosen)
        pool = [c for i, c in enumerate(pool) if i not in taken]
    return sets, pool


def _copy(i: int, bcx: int, *, locked: bool = False) -> dict:
    return {"uid": f"C-{i:06d}", "bcx": bcx, "card_detail_id": 12, "edition": 13, "foil": 0,
            "warnings": [cc.LOCK_WARNING] if locked else []}


def test_seeded_randomized_exact_equivalence_thousands_of_pools():
    rng = random.Random(9336)
    for case in range(3000):
        n = rng.choices([0, 1, 2, 3, 8, 30, 100], [8, 10, 16, 20, 20, 18, 8])[0]
        if case % 5 == 0:
            values = [1] * n
        else:
            values = [rng.choice([1, 1, 1, 2, 3, 4, 6, 8, 11, 20]) for _ in range(n)]
        # Exercise ordering ties, max boundaries, exact-two pools and mixed duplicates.
        if case % 11 == 0 and n >= 2:
            values[:2] = [7, 7]
        rng.shuffle(values)
        copies = [_copy(i, value, locked=(i % 17 == 0)) for i, value in enumerate(values)]
        maximum = rng.choice([1, 2, 3, 7, 11, 20, 40, 100, sum(values), max(values, default=1) - 1])
        assert cc.best_combine_set(copies, maximum) == _reference_best_combine_set(copies, maximum), case
        free = list(copies)
        rng.shuffle(free)
        thresholds = [1, 3, 6, 11, 20, 40]
        actual = cc.combine_sets(free, maximum, thresholds, 1000.0)
        expected = _reference_combine_sets(free, maximum, thresholds, 1000.0)
        assert actual == expected, case


def test_uniform_3282_one_cc_copies_match_reference_and_pack_20_sets():
    free = [_copy(i, 1, locked=(i % 101 == 0)) for i in range(3282)]
    assert cc.best_combine_set(free, 11) == _reference_best_combine_set(free, 11)
    assert cc.combine_sets(free, 11, [1, 3, 6, 11], 1000.0) == _reference_combine_sets(
        free, 11, [1, 3, 6, 11], 1000.0)
    sets, leftover = cc.combine_sets(free, 11, [1, 3, 6, 11], 1000.0)
    assert len(sets) == cc.MAX_SETS_PER_PRINT
    assert len(leftover) == 3282 - 20 * 11


def test_max_cc_and_exactly_two_input_edges_match_reference():
    pools = [
        [_copy(0, 5), _copy(1, 6)],
        [_copy(0, 5), _copy(1, 6)],
        [_copy(0, 5), _copy(1, 6)],
        [_copy(0, 5), _copy(1, 6), _copy(2, 1)],
    ]
    maxima = [10, 11, 12, 11]
    for copies, maximum in zip(pools, maxima):
        assert cc.best_combine_set(copies, maximum) == _reference_best_combine_set(copies, maximum)
        assert cc.combine_sets(copies, maximum, [1, 5, 11], 1500.0) == _reference_combine_sets(
            copies, maximum, [1, 5, 11], 1500.0)
