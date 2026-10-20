"""Slot-specific eviction scoring (2026-08-24). Editor-polish removed the misleading
"evicts X" LABEL from the candidate picker -- "evicts XYZ" was unclear and could refer
to a card that is neither removed nor replaced -- but the
underlying MATH survived: `ceres/candidates.py::rank_candidates` accepted a `slot`
parameter and never read it anywhere except a cosmetic `target_slot_id` label
(`app.js:2361` -> `routes_candidates.py:33` -> `candidates.py:369` -> `candidates.py:469`,
confirmed dead by grep), so a full plot's eviction search ran against the WHOLE roster
(`praetoria/land_reallocate.py::score_candidates_for_plot`'s `target_slot_occupants`,
plural) and kept whichever occupant scored best across the entire plot -- never
specifically the occupant of the slot the user actually clicked.

Quantified against the real player0005 snapshot (~/.ceres/snapshots/land-player0005-latest.json)
BEFORE this fix: 93 of 123 (75.6%) occupied-slot clicks on the snapshot's 25 full worker
plots showed a top recommendation scored against a DIFFERENT occupant than the one
actually in the clicked slot -- not a latent bug, the dominant real-world behaviour.

FIX (this file's subject): `rank_candidates` now resolves the picker's 0-based UI
`slot` index to the plot's real, 1-based worker slot (`real_slot = slot + 1` --
`app.js:2179-2183`'s `editorGroups` builds `workerSlots[index]` as the worker whose real
`slot` field equals `index + 1`), finds that SPECIFIC occupant in `plot.cards`, and
passes it alone as `score_candidates_for_plot`'s existing `target_slot_occupant`
(singular) parameter -- a capability that already existed and was already used correctly
elsewhere in the same file (`_donor_delta`, `candidates.py:325-361`), just never wired
into this call site. `praetoria/land_reallocate.py` needed NO changes.

Fail-safe design (deliberate, not an oversight):
- `slot` genuinely missing, stale, or out of range for this plot -> falls back to the
 OLD whole-roster search (`_resolve_real_slot` returns `None`, or no `plot.cards` entry
 matches). Never guesses "slot 1".
- An empty slot click was never affected either way -- no eviction search runs when the
 plot isn't full, slot-specific or otherwise.

A solo BLOODLINE holder is no longer a special case here (2026-08-26 ruling, supersedes
the earlier 2026-08-21 hard-refusal ruling): it scores for eviction exactly like any other
occupant of a full slot -- the `slot_holder_protected` exclusion code and gate are gone.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ceres.candidates import rank_candidates
from ceres.board import load_details_by_id

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)

DETAILS = {
    40001: {"id": 40001, "name": "Ordinary", "color": "Red", "sub_type": "Unrelated"},
    40002: {"id": 40002, "name": "Blue Ordinary", "color": "Blue", "sub_type": "Unrelated"},
    # A Land/Verico card carrying a BLOODLINE ("Toil and Kin") ability at level 10, no
    # other rate/sub_type levels below -- matches `test_candidates_ranking.py`'s pattern.
    866: {
        "id": 866, "name": "Bloodline Holder", "color": "Green", "sub_type": "Djinn",
        "stats": {"land_abilities": [[[]] for _ in range(9)] + [[["BLOODLINE", 0.1, "Djinn"]]]},
    },
}

_STAKING_FACTS_BASE = {
    "total_base_pp_cap": 1000000, "is_power_core_staked": True, "is_energized": False,
    "is_runi_staked": False, "deed_rarity_boost": 0, "totem_boost": 0, "title_boost": 0,
    "runi_boost": 0, "deed_status_token_boost": 0, "red_biome_modifier": 0,
    "blue_biome_modifier": 0, "white_biome_modifier": 0, "black_biome_modifier": 0,
    "green_biome_modifier": 0, "gold_biome_modifier": 0,
}


def _worker(name: str, slot: int, *, base: float = 5000, ref: str | None = None, detail_id: int = 40001, level: int | None = None) -> dict:
    ref = ref or name.lower().replace(" ", "-")
    return {
        "name": name, "card_detail_id": detail_id, "land_base_pp": base, "level": level,
        "total_harvest_pp": base, "asset_ref": ref, "stake_ref_uid": "deed-slot-test",
        "asset_kind": "worker_card", "slot": slot,
    }


def _idle_candidate(ref: str, base: float = 8000) -> dict:
    return {
        "asset_ref": ref, "uid": ref, "classification": {"bucket": "unstaked_apparent"},
        "facts": {"uid": ref, "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None, "card_detail_id": 40002, "land_base_pp": base, "level": None},
    }


def _report(*, capacity: int, workers: list[dict], runi: bool = False, idle_refs: tuple[str, ...] = ()) -> dict:
    staking_facts = dict(_STAKING_FACTS_BASE)
    staking_facts["max_workers_allowed"] = capacity
    staking_facts["is_runi_staked"] = runi
    report_workers = list(workers)
    if runi:
        report_workers.append({
            "name": "Runi", "card_detail_id": None, "land_base_pp": 0, "total_harvest_pp": 0,
            "asset_ref": "runi-asset", "stake_ref_uid": "deed-slot-test",
            "asset_kind": "runi_worker_card", "stake_type_uid": "STK-LND-RUNI", "slot": 0,
        })
    # `build_plots` (`praetoria/land_reallocate.py::_level_by_asset_ref`) reads a staked
    # worker's LEVEL from the collection sweep (`report["cards"]`), never from the
    # worker row's own "level" field (staked workers carry no level field of their own
    # in the real API) -- so a level-carrying worker needs a matching bare `cards[]` row
    # here. No `classification` key, so the idle-card scan skips it entirely; it exists
    # only to feed level lookup.
    level_source_rows = [
        {"asset_ref": w["asset_ref"], "facts": {"level": w["level"]}}
        for w in workers if w.get("level") is not None
    ]
    return {
        "plots": [{
            "deed_uid": "deed-slot-test",
            "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
            "staking_facts": staking_facts,
        }],
        "workers": report_workers,
        "cards": [_idle_candidate(ref) for ref in idle_refs] + level_source_rows,
        "unassigned_items": {},
    }


def test_slot_targets_the_specific_occupant_not_whole_roster():
    """Fixture data. Two-worker full plot: slot 1 = "Cheap Worker" (low base, easy to
 beat), slot 2 = "Strong Worker" (high base). Clicking slot 2 (real slot 2, UI index
 1) must evict "Strong Worker" -- NOT "Cheap Worker", even though evicting "Cheap
 Worker" would score higher and is exactly what the old whole-roster search would
 have picked."""
    report = _report(capacity=2, workers=[
        _worker("Cheap Worker", 1, base=100),
        _worker("Strong Worker", 2, base=50000),
    ], idle_refs=("candidate-a",))
    report["cards"][0]["facts"]["land_base_pp"] = 60000  # must beat "Strong Worker" to score positive
    details = dict(DETAILS)
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=details, slot=1,  # UI index 1 -> real slot 2
    )
    assert body["candidates"], "expected at least one scored candidate"
    top = body["candidates"][0]
    assert top["evicted"]["name"] == "Strong Worker"


def test_slot_boundary_first_and_last_slot_resolve_correctly():
    """Fixture data. Five-worker full plot (capacity 5, the real product's max today).
 Clicking UI slot index 0 (real slot 1) and UI slot index 4 (real slot 5) must each
 evict exactly that slot's own occupant."""
    workers = [_worker(f"Worker {n}", n, base=1000 * n) for n in range(1, 6)]
    report = _report(capacity=5, workers=workers, idle_refs=("candidate-a",))
    for ui_index, expected_name in ((0, "Worker 1"), (4, "Worker 5")):
        body = rank_candidates(
            report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
            include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=ui_index,
        )
        assert body["candidates"], f"expected candidates for slot index {ui_index}"
        assert body["candidates"][0]["evicted"]["name"] == expected_name


def test_runi_occupied_plot_never_targets_runi_for_eviction():
    """Fixture data. `max_workers_allowed` counts monster-card slots only -- a staked
 Runi occupies its own reserved slot 0 and is counted inside `worker_count`, never
 inside `max_workers_allowed` () -- so `land_reallocate.build_plots` takes
 `capacity = max_workers_allowed` as-is, with no Runi adjustment. Runi never appears
 in `plot.cards` at all (its `asset_kind` is `runi_worker_card`, excluded by
 `build_plots`'s worker-card filter), so `_resolve_real_slot`'s `slot + 1` (minimum
 1) can structurally never resolve to it. Two real worker slots (1, 2) fill this
 plot's capacity of 2; clicking either must evict the correct worker and never
 mention Runi."""
    report = _report(capacity=2, workers=[
        _worker("Worker One", 1, base=1000),
        _worker("Worker Two", 2, base=2000),
    ], runi=True, idle_refs=("candidate-a",))
    for ui_index, expected_name in ((0, "Worker One"), (1, "Worker Two")):
        body = rank_candidates(
            report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
            include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=ui_index,
        )
        assert body["candidates"]
        evicted_name = body["candidates"][0]["evicted"]["name"]
        assert evicted_name == expected_name
        assert evicted_name != "Runi"


def test_runi_plot_with_one_free_monster_slot_scores_a_plain_add_not_an_eviction():
    """Regression for (2026-08-26). A Runi plot's `max_workers_allowed` counts
 monster-card slots only -- it does NOT already have the Runi subtracted out. A live
 plot with `max_workers_allowed: 5` and 4 staked monster workers therefore has one
 genuinely free monster-card slot (5 - 4 = 1), matching real player0005 plot 41-9-56.
 Before the fix, `build_plots` wrongly computed `capacity = max_workers_allowed - 1
 = 4`, read as already full by 4 workers, and scored a plain add as an eviction.
 After the fix, clicking the free slot must add the candidate with no eviction and a
 positive score."""
    report = _report(capacity=5, workers=[
        _worker("Worker One", 1, base=1000),
        _worker("Worker Two", 2, base=1000),
        _worker("Worker Three", 3, base=1000),
        _worker("Worker Four", 4, base=1000),
    ], runi=True, idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=4,  # slot index 4 -> real slot 5, the free one
    )
    assert body["candidates"]
    top = body["candidates"][0]
    assert top["evicted"] is None
    assert top["plot_impact"]["value"] > 0


def test_empty_slot_click_runs_no_eviction_search():
    """Fixture data. Plot has capacity 3 but only 1 worker staked -- clicking an empty
 slot on this non-full plot must never surface an eviction: there is room to add the
 candidate."""
    report = _report(capacity=3, workers=[_worker("Sole Worker", 1, base=1000)], idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=1,
    )
    assert body["candidates"]
    assert all(c["evicted"] is None for c in body["candidates"])


@pytest.mark.parametrize("capacity", (2, 3))
def test_occupied_slot_is_netted_on_full_and_non_full_plots_alike(capacity):
    """The same occupied-slot replacement must net the same Base PP on both capacities."""
    workers = [
        _worker("Other Worker", 1, base=1000),
        _worker("Clicked Worker", 2, base=5000),
    ]
    report = _report(capacity=capacity, workers=workers, idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=1,
    )
    assert body["candidates"]
    assert body["candidates"][0]["base_pp_delta"]["value"] == 3000


def _non_full_occupied_report() -> dict:
    report = _report(capacity=3, workers=[
        _worker("Cheap Worker", 1, base=100),
        _worker("Strong Worker", 2, base=50000),
    ], idle_refs=("candidate-a",))
    report["cards"][0]["facts"]["land_base_pp"] = 60000
    return report


def test_occupied_slot_on_non_full_plot_names_that_slots_occupant_as_evicted():
    """An occupied non-full slot must identify its own occupant, not the cheapest roster victim."""
    body = rank_candidates(
        _non_full_occupied_report(), "deed-slot-test", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=1,
    )
    assert body["candidates"]
    assert body["candidates"][0]["evicted"]["name"] == "Strong Worker"


def test_occupied_slot_on_non_full_plot_still_yields_a_point_net_gain():
    """Removing the clicked occupant before scoring must preserve point net-gain scoring."""
    body = rank_candidates(
        _non_full_occupied_report(), "deed-slot-test", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=1,
    )
    assert body["candidates"]
    assert body["candidates"][0]["plot_impact"]["status"] == "point"
    assert body["estimate_counts"]["point"] > 0


def test_empty_slot_on_non_full_plot_is_still_a_plain_add():
    """An empty slot on a non-full plot remains an unnetted add with no eviction."""
    report = _report(capacity=3, workers=[_worker("Sole Worker", 1, base=1000)], idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=1,
    )
    assert body["candidates"]
    assert all(c["evicted"] is None for c in body["candidates"])
    assert all(c["base_pp_delta"]["value"] == 8000 for c in body["candidates"])


def test_non_full_plot_with_no_slot_supplied_is_a_plain_add_never_a_roster_search():
    """An omitted slot on a non-full plot remains a plain add, never a roster eviction."""
    report = _report(capacity=3, workers=[_worker("Sole Worker", 1, base=1000)], idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS),
    )
    assert body["candidates"]
    assert all(c["evicted"] is None for c in body["candidates"])
    assert all(c["base_pp_delta"]["value"] == 8000 for c in body["candidates"])


def test_solo_bloodline_holder_slot_is_now_a_normal_eviction_target():
    """Fixture data, reusing `test_candidates_ranking.py`'s BLOODLINE-holder table shape.
 A full plot whose slot 1 is a SOLO BLOODLINE holder (no companion sharing its
 sub_type on the plot) is no longer refused as an eviction target (2026-08-26
 ruling, supersedes the 2026-08-21 hard-refusal ruling) -- clicking its slot
 scores it against incoming candidates exactly like any other occupant, and a
 strongly better candidate evicts it."""
    report = _report(capacity=2, workers=[
        _worker("Bloodline Holder", 1, base=150, detail_id=866, level=10),
        _worker("Ordinary Companion", 2, base=5000, detail_id=40001),
    ], idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=0,  # slot 1 = the holder
    )
    assert body["candidates"]
    assert body["candidates"][0]["evicted"]["name"] == "Bloodline Holder"


def test_slot_missing_falls_back_to_whole_roster_search():
    """Fixture data. When `slot` is not provided at all (the FastAPI query param is
 genuinely omitted -- `routes_candidates.py` now passes `None` through rather than
 collapsing it to `0`), the old whole-roster search runs, exactly as it did before
 this fix -- a deliberate fail-safe, not an accidental regression. Verifies the
 fallback picks the highest-scoring eviction across the WHOLE roster (here, "Cheap
 Worker") rather than defaulting to "slot 1"'s occupant ("Cheap Worker" IS slot 1
 here, so this also proves the fallback is genuinely whole-roster reasoning and not a
 coincidental slot-1 guess -- see the companion assertion below with slots swapped)."""
    report = _report(capacity=2, workers=[
        _worker("Cheap Worker", 1, base=100),
        _worker("Strong Worker", 2, base=50000),
    ], idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS),  # slot omitted -> None
    )
    assert body["candidates"]
    assert body["candidates"][0]["evicted"]["name"] == "Cheap Worker"

    # Swap which slot holds the cheap worker -- the fallback must still find the
    # cheapest-to-evict occupant WHEREVER it sits, proving this is whole-roster search
    # not a "slot 1" guess.
    swapped = _report(capacity=2, workers=[
        _worker("Strong Worker", 1, base=50000),
        _worker("Cheap Worker", 2, base=100),
    ], idle_refs=("candidate-a",))
    body2 = rank_candidates(
        swapped, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS),
    )
    assert body2["candidates"][0]["evicted"]["name"] == "Cheap Worker"


def test_unresolvable_slot_falls_back_to_whole_roster_search():
    """Fixture data. `slot` provided but out of range for this plot (no `plot.cards`
 entry has that real slot number) -- same fail-safe fallback as the missing case,
 proven by the same "wherever the cheap worker sits" pattern."""
    report = _report(capacity=2, workers=[
        _worker("Cheap Worker", 1, base=100),
        _worker("Strong Worker", 2, base=50000),
    ], idle_refs=("candidate-a",))
    body = rank_candidates(
        report, "deed-slot-test", "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=dict(DETAILS), slot=99,
    )
    assert body["candidates"]
    assert body["candidates"][0]["evicted"]["name"] == "Cheap Worker"


# THE regression test: the real "Grandmaster Rathe" vs "Shin-Lo" case found during
# investigation, replayed against the real player0005 snapshot. Fails on pre-fix code
# (top pick evicted "Shin-Lo" regardless of which slot was clicked; scored 14,100 for
# the whole-roster pick vs 10,800 for the correct slot-specific one), passes after.

SNAPSHOT_PATH = Path.home() / ".ceres" / "snapshots" / "land-player0005-latest.json"


def test_real_snapshot_grandmaster_rathe_regression():
    """REAL snapshot data (~/.ceres/snapshots/land-player0005-latest.json), skipped if that
 file isn't present in this environment. Plot `I-290-4bb57b41841c5a` is at full
 worker capacity; "Grandmaster Rathe" is one of its real occupants. Before this fix,
 clicking Rathe's slot showed a top recommendation evicting "Shin-Lo" instead (a
 DIFFERENT slot's occupant) -- exactly the defect this file closes."""
    import pytest
    if not SNAPSHOT_PATH.exists():
        pytest.skip(f"real snapshot not present at {SNAPSHOT_PATH}")
    report = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    deed_uid = "I-290-4bb57b41841c5a"
    plot_row = next((p for p in report["plots"] if p["deed_uid"] == deed_uid), None)
    if plot_row is None:
        pytest.skip("expected real plot not present in this snapshot")
    rathe = next((w for w in report["workers"] if w.get("stake_ref_uid") == deed_uid and w.get("name") == "Grandmaster Rathe"), None)
    if rathe is None:
        pytest.skip("Grandmaster Rathe is no longer staked on this plot in the current snapshot")
    real_slot = rathe["slot"]
    ui_index = real_slot - 1
    details_by_id = load_details_by_id()
    body = rank_candidates(
        report, deed_uid, "worker", player="player0005", now=NOW, bucket="idle",
        include_cooling=True, limit=5, details_by_id=details_by_id, slot=ui_index,
    )
    assert body["candidates"], "expected at least one scored candidate for this real plot/slot"
    top_evicted = body["candidates"][0]["evicted"]
    assert top_evicted is not None
    assert top_evicted["name"] == "Grandmaster Rathe", (
        f"clicking Grandmaster Rathe's slot must evict Grandmaster Rathe, not {top_evicted['name']!r}"
    )
