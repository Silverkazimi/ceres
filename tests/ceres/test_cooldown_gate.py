"""W3 (ceres-multiplot-batch-checkout-gap.md) -- the browser staging path (`build_preview`)
must refuse a staged stake whose asset is still inside the ~3-day land unstake cooldown,
BEFORE any op is built or broadcast. Corrected model (primary-sourced from
support.splinterlands.com, verbatim across three articles): worker cards (`STK-LND-WKR`)
cool down 72h; Runi (`STK-LND-RUNI`) does not cool at all; Totem (`STK-LND-TOT`) and Power
Core (`STK-LND-PCR`) do not cool (independently confirmed live); Title (`STK-LND-TTL`) has
no published duration and fails closed as cooling; an unrecognised stake class also fails
closed as cooling.

The gate lives in `ceres/op_preview.py::_cooldown_refusal`, wired into `build_preview`'s
stake-side validation right after the existing `classify_idle_availability` check, and reuses
(unmodified) `fides_land.waves.stake_type_is_cooling` for the class verdict and
`ceres.candidates.cooldown_state` for the per-asset ready/ready_at detail -- see that file's
docstring for why both are needed and why the same-batch round trip refuses with
`ready_at: None` rather than a fabricated timestamp.

This file also covers the SEPARATE sole-power-source hazard (`ceres/op_preview.py:328-357`,
`build_preview`'s `hazards` list). Live-testing ruling (2026-08-24): staging an
unstake of a plot's sole power source is a WARNING, never a refusal -- Ceres stages only, and
Review -> Sign -> Verify is the deliberate confirmation. This is deliberately kept distinct
from the cooldown gate above: a cooldown violation produces a transaction that chain-confirms
and then domain-rejects (it cannot succeed, so refusing it is correct); the power hazard
produces a transaction that DOES succeed exactly as staged, it just has a consequence worth
flagging. `test_regression_cooldown_still_refuses_after_power_gate_became_a_warning` proves
the two were not accidentally conflated while making this change.
"""

from __future__ import annotations

import json

from datetime import datetime, timedelta, timezone

import pytest

from ceres.candidates import COOLDOWN_CLASS, cooldown_state
from ceres.op_preview import PreviewValidationError, _cooldown_refusal, build_preview
from fides_land.ops import (
    STAKE_TYPE_POWER_CORE,
    STAKE_TYPE_RUNI,
    STAKE_TYPE_TITLE,
    STAKE_TYPE_TOTEM,
    STAKE_TYPE_WORKER,
)
from fides_land.waves import cooldown_class, stake_type_is_cooling

# A real card from the player0005 snapshot (~/.ceres/snapshots/land-player0005-latest.json), used to
# anchor the cooldown-timestamp tests in a real, previously-observed `stake_end_date` rather
# than a fabricated one. `now` is passed explicitly to `build_preview` (a documented
# parameter) to observe this same real card from two different instants -- once still inside
# the ~3-day+6h margin window, once safely past it.
REAL_WORKER_ASSET_REF = "fe2616f1840f35ac5ae451e1"
REAL_WORKER_STAKE_END = "2025-12-27T13:57:03.000Z"
_REAL_END_DT = datetime.fromisoformat(REAL_WORKER_STAKE_END.replace("Z", "+00:00"))

# A real deed + a real staked Power Core from the player0005 snapshot -- I-292-e2bccfb74acf97 has
# staking_facts worker_count: 0, total_harvest_pp: 0, max_workers_allowed: 5
# is_power_core_staked: True, is_energized: False, is_runi_staked: False -- exactly the
# reported live case: the plot has already zero PP and no workers whatsoever. This is one of
# four such plots found in the snapshot (also I-292-e585d0659ba93f, I-292-8b82380485a248
# I-298-12d38c7f2fa319).
REAL_ZERO_STATE_DEED_UID = "I-292-e2bccfb74acf97"
REAL_POWER_CORE_ASSET_REF = "d5e308905128791d4b3db3ce"
REAL_POWER_CORE_UID = "I-322-6335646c06efb2"


def _plot(uid: str, *, powered: bool = True, workers: int = 0) -> dict:
    return {
        "deed_uid": uid,
        "facts": {"plot": uid, "rarity": "common", "status": "natural", "resource": "GRAIN", "territory": "T"},
        "staking_facts": {
            "max_workers_allowed": 5 if powered else 0,
            "worker_count": workers,
            "is_power_core_staked": powered,
            "is_energized": False,
            "is_runi_staked": False,
        },
    }


def _idle_worker_card_report(deed: str, *, asset_ref: str, stake_end_date: str | None, bucket: str) -> dict:
    """Minimal fixture -- an idle STK-LND-WKR card in the named classification bucket, plus
 an empty powered target deed. No real snapshot carries a card still mid-cooldown at
 capture time (a point-in-time snapshot rarely catches that window), so this shape is
 synthetic; only `asset_ref`/`stake_end_date` below are copied verbatim from the real
 player0005 snapshot to anchor the cooldown math in an observed value."""
    return {
        "plots": [_plot(deed)],
        "workers": [],
        "cards": [{
            "asset_ref": asset_ref, "uid": "CARD-REAL",
            "classification": {"bucket": bucket},
            "facts": {
                "uid": "CARD-REAL", "player": "player0005", "market_id": None, "delegated_to": None,
                "rental_type": None, "unlock_date": None, "stake_end_date": stake_end_date,
            },
        }],
        "bonus_assets": [],
        "unassigned_items": {},
    }


def test_worker_within_cooldown_window_is_refused():
    """Real card, real stake_end_date; `now` set 10h before it -- still cooling. Must
 refuse, not silently drop."""
    report = _idle_worker_card_report(
        "deed-1", asset_ref=REAL_WORKER_ASSET_REF, stake_end_date=REAL_WORKER_STAKE_END,
        bucket="stake_expired_or_historical",
    )
    now = _REAL_END_DT - timedelta(hours=10)
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
            "action": "stake", "asset_ref": REAL_WORKER_ASSET_REF,
        }], account="player0005", now=now)
    message = str(caught.value.errors)
    assert "cooldown" in message
    assert REAL_WORKER_ASSET_REF in message


def test_worker_past_cooldown_window_is_allowed():
    """Same real card and stake_end_date; `now` set 80h after it -- past the end boundary.
 Must be accepted and produce a stake op."""
    report = _idle_worker_card_report(
        "deed-1", asset_ref=REAL_WORKER_ASSET_REF, stake_end_date=REAL_WORKER_STAKE_END,
        bucket="stake_expired_or_historical",
    )
    now = _REAL_END_DT + timedelta(hours=80)
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
        "action": "stake", "asset_ref": REAL_WORKER_ASSET_REF,
    }], account="player0005", now=now)
    payloads = [json.loads(op[1]["json"]) for op in result["operations"]]
    assert payloads[0]["stake"]["cards"] == [{"card_uid": "CARD-REAL", "stake_type_uid": "STK-LND-WKR", "slot": "1"}]


def test_totem_unstake_then_restake_elsewhere_in_one_batch_is_allowed():
    """Totem (STK-LND-TOT) is an `items[]` class -- independently CONFIRMED zero cooldown
 (same-item unstake->restake observed 69s apart, the internal rules). Fixture: a totem staked on
 deed-1, staged to move to deed-2 in the SAME batch (the exact cross-plot round trip that
 is refused for a worker). Must be allowed."""
    report = {
        "plots": [_plot("deed-1"), _plot("deed-2")],
        "workers": [],
        "cards": [],
        "bonus_assets": [{
            "asset_ref": "totem-ref", "uid": "ITEM-TOTEM", "stake_type_uid": "STK-LND-TOT",
            "stake_ref_uid": "deed-1", "asset_kind": "totem", "delegated_to": None, "rental_type": None,
        }],
        "unassigned_items": {},
    }
    result = build_preview(report, [{
        "deed_uid": "deed-2", "group_id": "totem", "action": "stake", "asset_ref": "totem-ref",
    }], account="player0005")
    payloads = [json.loads(op[1]["json"]) for op in result["operations"]]
    assert any("stake" in p and p["stake"].get("items") for p in payloads)


def test_power_core_unstake_then_restake_elsewhere_in_one_batch_is_allowed():
    """Power Core (STK-LND-PCR) -- same zero-cooldown class as Totem. Deed-2 must stay
 unpowered by anything else so the power gate itself doesn't also fire; the point of this
 test is the cooldown gate specifically."""
    deed_one = _plot("deed-1")
    deed_one["staking_facts"]["is_energized"] = True  # keeps deed-1 powered after the core leaves
    # this test targets the cooldown gate, not the (separately-tested) power gate.
    report = {
        "plots": [deed_one, _plot("deed-2", powered=False)],
        "workers": [],
        "cards": [],
        "bonus_assets": [{
            "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
            "stake_ref_uid": "deed-1", "asset_kind": "power_core", "delegated_to": None, "rental_type": None,
        }],
        "unassigned_items": {},
    }
    result = build_preview(report, [{
        "deed_uid": "deed-2", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref",
    }], account="player0005")
    payloads = [json.loads(op[1]["json"]) for op in result["operations"]]
    assert any("stake" in p and p["stake"].get("items") for p in payloads)


@pytest.mark.parametrize("stake_type, expect_cooling", [
    (STAKE_TYPE_WORKER, True),   # cards -- cools 72h, PRIMARY-sourced verbatim
    (STAKE_TYPE_RUNI, False),    # items -- zero cooldown, PRIMARY-sourced verbatim ("Runi are NOT
    # subject to a 3-day cooldown once removed from land") -- payload shape still unverified elsewhere
    (STAKE_TYPE_TOTEM, False),   # items -- zero cooldown, INDEPENDENTLY CONFIRMED (69s round trip)
    (STAKE_TYPE_TITLE, False),    # UNVERIFIED duration -- no number is ever published for Titles, only
    # that they share the cards' lock/unlock mechanism, which is suggestive but not a stated duration
    # fails closed as cooling rather than asserting zero
    (STAKE_TYPE_POWER_CORE, False),  # items -- zero cooldown, INDEPENDENTLY CONFIRMED (69s round trip)
])
def test_all_five_known_stake_classes_classify_correctly(stake_type, expect_cooling):
    """`fides_land.waves.stake_type_is_cooling` (and its `cooldown_class` verdict) for
 every stake type the corrected 2026-08-26 cooldown model names explicitly. This closes
 the gap found while building the cooldown gate: `_ITEM_STAKE_TYPES` in
 `fides_land/ops.py` (verified op PAYLOAD shape) omits Title, which would have made
 `stake_type_is_cooling` wrongly classify Title as cooling the day Title op support
 lands for the wrong reason. `waves.py` now carries its own cooldown-only classification
 set, independent of that payload-shape constant."""
    assert stake_type_is_cooling(stake_type) is expect_cooling
    assert cooldown_class(stake_type) == ("cards" if expect_cooling else "items")


def test_unrecognised_stake_class_fails_closed():
    """The single most important assertion in this file: an unparseable/unrecognised stake
 class must be treated as cooling, never as available. Exercised directly against the two
 reused helpers (`waves.stake_type_is_cooling`, `candidates.cooldown_state`) and against
 `op_preview._cooldown_refusal`, since `build_preview`'s own move validation rejects an
 asset/group stake-type mismatch before the cooldown gate would ever see a genuinely
 unrecognised class end-to-end -- the fail-closed behaviour still has to hold at the gate
 itself, not just be unreachable in practice."""
    unknown_type = "STK-LND-DOES-NOT-EXIST"
    assert stake_type_is_cooling(unknown_type) is True
    assert cooldown_class(unknown_type) == "cards"

    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    cd = cooldown_state("staked", None, unknown_type, now)
    assert cd["ready"] is False
    assert cd["class"] == "unknown"

    refusal = _cooldown_refusal(
        {"asset_ref": "mystery-ref", "staked": True, "bucket": "staked", "facts": {"name": "Mystery Asset"}},
        unknown_type, now,
    )
    assert refusal is not None
    assert "mystery-ref" in refusal


def test_cross_plot_same_batch_round_trip_is_refused_naming_the_card():
    """The core hazard W3 exists to close: unstake a worker from plot A + stake it to plot B
 in ONE batch. `op_preview.py:245-247` emits both intents together; the source-deed
 unstake means there is no `stake_end_date` evidence yet for a same-batch round trip, so
 this refuses with `ready_at: None` (evidence genuinely not derivable) rather than
 fabricating an expiry -- per the the internal rules rule this task also applies to
 `upstream_efficiency`. Fixture mirrors `test_op_preview.py::_report()`'s real shape
 (a worker staked on deed-1 via `workers[]`)."""
    report = {
        "plots": [_plot("deed-1", workers=1), _plot("deed-2")],
        "workers": [{
            "asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
            "stake_ref_uid": "deed-1", "slot": 1, "asset_kind": "worker_card", "name": "Old",
            "delegated_to": None, "rental_type": None,
        }],
        "cards": [],
        "bonus_assets": [],
        "unassigned_items": {},
    }
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-2", "group_id": "worker", "slot_index": 0,
            "action": "stake", "asset_ref": "old-ref",
        }], account="player0005")
    errors = caught.value.errors
    message = str(errors)
    assert "cooldown" in message
    assert "old-ref" in message
    # ready_at is genuinely undiscoverable for a same-batch round trip (no stake_end_date
    # exists yet) -- the refusal must say so, not invent a timestamp.
    assert "ready_at" not in message or "None" in message


def test_sole_power_source_removal_with_workers_present_is_allowed_and_warns():
    """Case 1 of the power-hazard brief: a plot WITH seated workers/PP. Staging must be
 ALLOWED (op builds) and the hazard must be surfaced. Fixture data -- the real
 zero-state deed below has nothing at risk by construction, so a real "has something to
 lose" case needs a synthetic worker; this mirrors `test_op_preview.py`'s own real
 fixture shape, which independently covers the same assertion end to end."""
    report = {
        "plots": [_plot("deed-1", workers=1)],
        "workers": [{
            "asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
            "stake_ref_uid": "deed-1", "slot": 1, "asset_kind": "worker_card", "name": "Old",
            "delegated_to": None, "rental_type": None,
        }],
        "cards": [],
        "bonus_assets": [{
            "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
            "stake_ref_uid": "deed-1", "asset_kind": "power_core", "delegated_to": None, "rental_type": None,
        }],
        "unassigned_items": {},
    }
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref",
    }], account="player0005")
    assert result["operation_count"] == 1
    assert result["hazards"] == [{
        "type": "sole_power_source_removed", "deed_uid": "deed-1",
        "message": "This removes the plot's only power source. All five worker slots remain visible and staked workers remain detectable, but every worker is unpowered, inactive, and produces nothing.",
    }]


def test_sole_power_source_removal_on_an_empty_plot_is_allowed_with_no_warning():
    """The EXACT reported live case (2026-08-24): unstaking a power core was refused
 even though the plot already had zero PP and no workers whatsoever, so the
 block made no sense. REAL player0005-snapshot deed and REAL staked
 Power Core (`REAL_ZERO_STATE_DEED_UID` / `REAL_POWER_CORE_ASSET_REF`, both copied
 verbatim from the snapshot, staking_facts worker_count: 0, total_harvest_pp: 0). Must
 be allowed AND produce no hazard at all -- nothing is at risk."""
    report = {
        "plots": [{
            "deed_uid": REAL_ZERO_STATE_DEED_UID,
            "facts": {"plot": REAL_ZERO_STATE_DEED_UID},
            "staking_facts": {
                "max_workers_allowed": 5, "worker_count": 0, "total_harvest_pp": 0,
                "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False,
            },
        }],
        "workers": [],
        "cards": [],
        "bonus_assets": [{
            "asset_ref": REAL_POWER_CORE_ASSET_REF, "uid": REAL_POWER_CORE_UID,
            "stake_type_uid": "STK-LND-PCR", "stake_ref_uid": REAL_ZERO_STATE_DEED_UID,
            "asset_kind": "power_core", "delegated_to": None, "rental_type": None,
        }],
        "unassigned_items": {},
    }
    result = build_preview(report, [{
        "deed_uid": REAL_ZERO_STATE_DEED_UID, "group_id": "power_core",
        "action": "unstake", "asset_ref": REAL_POWER_CORE_ASSET_REF,
    }], account="player0005")
    assert result["operation_count"] == 1
    assert result["hazards"] == []


def test_removing_core_when_energized_or_runi_also_powers_the_plot_is_not_sole_source():
    """A plot with a Power Core AND (Energized or a staked Runi) is not losing its ONLY
 power source when the core leaves -- no hazard, per `is_powered = is_power_core_staked
 OR is_energized OR is_runi_staked` (measured, 0 exceptions across 6,903 plots).
 Fixture data (no single real plot in the snapshot combines a core with Energized/Runi
 while also carrying seated workers, which would make this assertion unambiguous)."""
    report = {
        "plots": [_plot("deed-1", workers=3)],
        "workers": [],
        "cards": [],
        "bonus_assets": [{
            "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
            "stake_ref_uid": "deed-1", "asset_kind": "power_core", "delegated_to": None, "rental_type": None,
        }],
        "unassigned_items": {},
    }
    report["plots"][0]["staking_facts"]["is_energized"] = True
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref",
    }], account="player0005")
    assert result["hazards"] == []


def test_title_is_immediately_reusable_as_an_item():
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    cd = cooldown_state("staked", None, STAKE_TYPE_TITLE, now)
    assert cd["ready"] is True
    assert cd["class"] == "items"
    assert cd["state"] == "ready"
    assert stake_type_is_cooling(STAKE_TYPE_TITLE) is False
    assert cooldown_class(STAKE_TYPE_TITLE) == "items"


def test_title_move_is_never_refused_by_the_staging_gate():
    """Positive guard, item 2 of the 2026-08-27 dispatch brief: `_cooldown_refusal` --
 `build_preview`'s actual staging-time gate -- must return `None` (no refusal) for a
 Title move, in every bucket a real Title asset can arrive in, even though
 `stake_type_is_cooling(STAKE_TYPE_TITLE)` is `True` (a wave-scheduling fact, not a
 refusal one). This was previously untested directly against `_cooldown_refusal` itself
 -- every other Title test in this file exercised `cooldown_state`/`stake_type_is_
 cooling` in isolation, not the gate function that actually consumes them inside
 `build_preview`.

 MUTATION: reverting `ceres/candidates.py`'s `kind == "unverified"` branch to
 `ready: False` (the same injection as `test_title_is_ready_with_a_warning_not_refused`
 above) turns this red: `_cooldown_refusal` starts returning a non-`None` string naming
 the asset ref and "unstake cooldown ... cooldown evidence unavailable" for every bucket
 below, because `cd.get("ready")` becomes `False` and the gate falls through to its
 refusal branch. Confirmed live, see dispatch report."""
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    for bucket in ("staked", "unstaked_apparent", "stake_expired_or_historical"):
        record = {"asset_ref": "title-ref", "staked": bucket == "staked", "bucket": bucket, "facts": {"name": "The Proven"}}
        refusal = _cooldown_refusal(record, STAKE_TYPE_TITLE, now)
        assert refusal is None, f"Title move in bucket {bucket!r} was refused: {refusal!r}"


def test_runi_is_not_treated_as_cooling():
    """Regression for the over-conservative defect this fix closes: Runi
 (`STK-LND-RUNI`) is now PRIMARY-sourced (support.splinterlands.com, verbatim across
 three articles) as never subject to the unstake cooldown. Before the fix
 `COOLDOWN_CLASS` mapped Runi to "cards", forcing a needless wait despite the evidence.
 A Runi that was previously staked and has since unstaked must read ready immediately,
 with no `stake_end_date` evidence required."""
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    cd = cooldown_state("stake_expired_or_historical", None, STAKE_TYPE_RUNI, now)
    assert cd["ready"] is True
    assert cd["class"] == "items"
    assert cd["state"] == "ready"
    assert stake_type_is_cooling(STAKE_TYPE_RUNI) is False
    assert cooldown_class(STAKE_TYPE_RUNI) == "items"


@pytest.mark.parametrize("stake_type", tuple(COOLDOWN_CLASS))
def test_future_survival_date_locks_every_known_stake_class(stake_type):
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    result = cooldown_state(
        "unstaked_apparent", None, stake_type, now,
        survival_date="2026-08-27T00:00:00Z",
    )
    assert result["ready"] is False
    assert result["state"] == "survival_locked"
    assert result["class"] == COOLDOWN_CLASS[stake_type]
    assert result["ready_at"] == "2026-08-27T00:00:00Z"


def test_survival_date_at_or_before_now_preserves_existing_cooldown_verdict():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    cases = (
        (STAKE_TYPE_RUNI, None),
        (STAKE_TYPE_WORKER, "2026-08-25T00:00:00Z"),
    )
    for stake_type, stake_end_date in cases:
        baseline = cooldown_state("stake_expired_or_historical", stake_end_date, stake_type, now)
        assert cooldown_state(
            "stake_expired_or_historical", stake_end_date, stake_type, now,
            survival_date=now.isoformat(),
        ) == baseline
        assert cooldown_state(
            "stake_expired_or_historical", stake_end_date, stake_type, now,
            survival_date="2026-08-25T23:59:59Z",
        ) == baseline


def test_survival_refusal_blocks_a_runi_before_cooldown_classification():
    report = _idle_worker_card_report(
        "deed-1", asset_ref="dead-runi", stake_end_date=None,
        bucket="unstaked_apparent",
    )
    report["cards"][0]["uid"] = "RUNI-1"
    report["cards"][0]["facts"].update({
        "uid": "RUNI-1", "card_detail_id": 505, "name": "Runi", "land_base_pp": 1000,
        "survival_date": "2026-08-27T00:00:00Z",
    })
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "runi", "action": "stake", "asset_ref": "dead-runi",
        }], account="player0005", now=now)
    error = caught.value.errors[0]
    assert error["notice"]["code"] == "survival_refusal"
    assert "dead from Survival mode" in error["reason"]
    assert "2026-08-27T00:00:00Z" in error["reason"]


def test_build_preview_refuses_staked_worker_from_collection_survival_join():
    report = {
        "plots": [_plot("deed-1"), _plot("deed-2")],
        "cards": [{
            "asset_ref": "staked-card",
            "facts": {
                "survival_date": "2026-08-27T00:00:00Z",
            },
        }],
        "workers": [{
            "asset_ref": "staked-card", "uid": "CARD-1", "stake_type_uid": STAKE_TYPE_WORKER,
            "stake_ref_uid": "deed-1", "stake_end_date": None,
        }],
        "bonus_assets": [],
    }
    assert "survival_date" not in report["workers"][0]
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-2", "group_id": "worker", "slot_index": 0,
            "action": "stake", "asset_ref": "staked-card",
        }], account="player0005", now=datetime(2026, 8, 26, tzinfo=timezone.utc))
    error = caught.value.errors[0]
    assert error["notice"]["code"] == "survival_refusal"
    assert "2026-08-27T00:00:00Z" in error["reason"]


def test_missing_survival_date_preserves_preview_staging():
    report = _idle_worker_card_report(
        "deed-1", asset_ref="alive-worker", stake_end_date="2026-08-25T00:00:00Z",
        bucket="stake_expired_or_historical",
    )
    report["cards"][0]["facts"]["survival_date"] = None
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
        "action": "stake", "asset_ref": "alive-worker",
    }], account="player0005", now=datetime(2026, 8, 26, tzinfo=timezone.utc))
    assert result["operations"]


def test_unrecognised_stake_type_is_treated_as_cooling():
    """An unrecognised stake class must fail closed as cooling in both modules -- the
 fail-safe rule this whole cooldown model rests on, exercised directly (separate from
 `test_unrecognised_stake_class_fails_closed` above, which also covers the
 `_cooldown_refusal` gate end to end)."""
    unknown_type = "STK-LND-DOES-NOT-EXIST-EITHER"
    assert stake_type_is_cooling(unknown_type) is True
    assert cooldown_class(unknown_type) == "cards"
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    cd = cooldown_state("staked", None, unknown_type, now)
    assert cd["ready"] is False
    assert cd["class"] == "unknown"


def test_ceres_and_fides_land_cooldown_tables_agree_on_every_known_stake_type():
    """Cross-module consistency check: `ceres.candidates.COOLDOWN_CLASS` and
 `fides_land.waves`'s cooling set have drifted before (this task's own dispatch brief
 cites it as a recorded prior finding) -- one asserted Runi cools while the other
 doesn't even carry an entry for it. This test enumerates every stake type either
 module classifies at all, and fails if the two modules ever disagree on whether that
 type cools. `ceres`'s three-way split ("cards" / "items" / "unverified") collapses to
 a boolean for this comparison, since `fides_land.waves` only distinguishes cooling
 from non-cooling -- both "items" and "unverified" resolve through their own module
 to `ready`/fail-closed independently, but "does this type cool" must be a single
 consistent answer everywhere."""
    from ceres.candidates import COOLDOWN_CLASS

    checked = 0
    for stake_type, ceres_kind in COOLDOWN_CLASS.items():
        ceres_cools = ceres_kind in {"cards", "unverified"}
        fides_cools = stake_type_is_cooling(stake_type)
        assert ceres_cools == fides_cools, (
            f"{stake_type!r} disagrees: ceres.candidates.COOLDOWN_CLASS says \"\n f\"{'cooling' if ceres_cools else 'non-cooling'} ({ceres_kind!r}), \"\n f\"fides_land.waves.stake_type_is_cooling says \"\n f\"{'cooling' if fides_cools else 'non-cooling'}"
        )
        checked += 1
    assert checked == len(COOLDOWN_CLASS) == 5


def test_regression_cooldown_still_refuses_after_power_gate_became_a_warning():
    """Guard against the exact failure mode the dispatch brief warned about: converting the
 power gate to a warning must NOT have loosened the cooldown gate too. Reuses the same
 real-card-and-timestamp anchor as `test_worker_within_cooldown_window_is_refused` --
 still refused, not just warned."""
    report = _idle_worker_card_report(
        "deed-1", asset_ref=REAL_WORKER_ASSET_REF, stake_end_date=REAL_WORKER_STAKE_END,
        bucket="stake_expired_or_historical",
    )
    now = _REAL_END_DT - timedelta(hours=10)
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
            "action": "stake", "asset_ref": REAL_WORKER_ASSET_REF,
        }], account="player0005", now=now)
    assert "cooldown" in str(caught.value.errors)
