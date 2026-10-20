from __future__ import annotations

import json

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fides_land.ops import LandOpError
from ceres import capture as capture_mod
from ceres import op_preview as op_preview_mod
from ceres import routes_op_preview as routes_op_preview_mod
from ceres.candidates import rank_candidates
from ceres.config import CeresConfig
from ceres.op_preview import PreviewValidationError, build_preview
from ceres.routes_op_preview import plot_op_preview
from praetoria.land_analysis import normalize_collection_worker, stable_asset_ref


LAND_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "land"


def _plot(uid: str, *, powered: bool = True, workers: int = 1) -> dict:
    return {
        "deed_uid": uid,
        "facts": {"plot": uid, "rarity": "common", "status": "natural", "resource": "GRAIN", "territory": "T", "worksite_type": "Grain Farm"},
        "worksite_facts": {"project_type": "worksite_grain"},
        "staking_facts": {
            "max_workers_allowed": 5 if powered else 0,
            "worker_count": workers,
            "is_power_core_staked": powered,
            "is_energized": False,
            "is_runi_staked": False,
        },
    }


def _report() -> dict:
    deed = "deed-1"
    return {
        "plots": [_plot(deed)],
        "workers": [{
            "asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
            "stake_ref_uid": deed, "slot": 1, "asset_kind": "worker_card", "name": "Old",
            "delegated_to": None, "rental_type": None,
        }],
        "cards": [{
            "asset_ref": "new-ref", "uid": "CARD-NEW",
            "facts": {"uid": "CARD-NEW", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None},
        }],
        "bonus_assets": [],
        "unassigned_items": {},
    }


def _stale_stake_report() -> tuple[dict, str]:
    card = json.loads((LAND_FIXTURE_ROOT / "card_stale_stake.json").read_text(encoding="utf-8"))
    asset_ref = stable_asset_ref(card["uid"])
    report = {
        "plots": [_plot("target-deed", workers=0), _plot(card["stake_ref_uid"], workers=0)],
        "workers": [],
        "cards": [{
            "asset_ref": asset_ref,
            "classification": {"bucket": "stake_expired_or_historical"},
            "facts": card,
        }],
        "bonus_assets": [],
        "unassigned_items": {},
    }
    return report, asset_ref


def _as_live_read(expand):
    """: the preview's slot check now reads through capture.read_plots_live (in memory, no
    store); adapt the old single-deed expand fakes to that contract so their assertions stay as-is."""
    def read(config, account, deed_uids, report):
        return {"report": expand(config, account, list(deed_uids)[0], include_collection=False), "failed": []}
    return read


@pytest.mark.parametrize("slot_state", ["absent", "none"])
def test_worker_with_unknown_slot_refuses_before_signing(slot_state):
    """G1: a present worker without slot evidence must fail closed as UNKNOWN.

    Mutation injection: make the unknown case fall through to permission; this must
    go red because the preview would emit a signable operation.
    """
    report = _report()
    if slot_state == "absent":
        del report["workers"][0]["slot"]
    else:
        report["workers"][0]["slot"] = None
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "new-ref", "slot_index": 0,
        }], account="player0005")
    error = caught.value.errors[0]
    assert error["notice"]["code"] == "worker_slot_unknown"
    assert error["reason"] == "worker slot occupancy is unknown after expanding the plot; refusing rather than guessing"
    assert error["notice"]["message"] == "Ceres still does not know which worker slots are occupied. Expand this plot to load it, then try again."


def test_known_worker_slot_refuses_taken_and_allows_free():
    """G2: known occupancy still refuses a taken slot while a genuinely free slot stays allowed.

    Mutation injection: refuse every claimed worker slot; the free-slot preview must
    still produce one operation and go red.
    """
    report = _report()
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "new-ref", "slot_index": 0,
        }], account="player0005")
    assert caught.value.errors[0]["notice"]["code"] == "worker_slot_taken"
    assert caught.value.errors[0]["reason"] == "worker slot(s) already occupied in the current snapshot: [1]"
    assert caught.value.errors[0]["notice"]["message"] == "The worker slot you chose is already occupied. Choose a different slot."

    free = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref", "slot_index": 1,
    }], account="player0005")
    assert free["operation_count"] == 1


def test_uncertain_worker_claim_expands_only_that_deed_once_and_rechecks_slots(monkeypatch):
    report = _report()
    report["plots"].append(_plot("unclaimed-deed", workers=1))
    del report["workers"][0]["slot"]
    expanded = _report()
    expanded["workers"][0]["slot"] = 1
    calls = []

    def expand(config, account, deed_uid, *, include_collection):
        calls.append((config, account, deed_uid, include_collection))
        return expanded

    monkeypatch.setattr(capture_mod, "read_plots_live", _as_live_read(expand))
    config = object()
    result = build_preview(
        report,
        [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 1}],
        account="player0005",
        config=config,
    )

    assert result["operation_count"] == 1
    assert calls == [(config, "player0005", "deed-1", False)]


def test_worker_slot_expand_failure_stays_fail_closed(monkeypatch):
    report = _report()
    del report["workers"][0]["slot"]
    calls = []

    def expand(config, account, deed_uid, *, include_collection):
        calls.append(deed_uid)
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(capture_mod, "read_plots_live", _as_live_read(expand))
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(
            report,
            [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0}],
            account="player0005",
            config=object(),
        )

    assert calls == ["deed-1"]
    assert caught.value.errors[0]["notice"]["code"] == "worker_slot_unknown"


def test_residual_worker_count_mismatch_has_its_own_refusal(monkeypatch):
    report = _report()
    report["plots"][0]["staking_facts"]["worker_count"] = 2
    calls = []

    def expand(config, account, deed_uid, *, include_collection):
        calls.append(deed_uid)
        return report

    monkeypatch.setattr(capture_mod, "read_plots_live", _as_live_read(expand))
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(
            report,
            [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 1}],
            account="player0005",
            config=object(),
        )

    assert calls == ["deed-1"]
    assert caught.value.errors[0]["notice"]["code"] == "worker_count_mismatch"


def test_expansion_refreshes_bulk_worker_count_and_refuses_only_slot_six(monkeypatch):
    report = _report()
    report["generated_at"] = "2026-09-04T06:00:00Z"
    report["plots"][0] = _plot("deed-1", powered=False, workers=3)
    report["plots"].append(_plot("capacity-peer", powered=True, workers=0))
    report["workers"][0]["slot"] = 1
    report["workers"].append({
        "asset_ref": "old-ref-2", "uid": "CARD-OLD-2", "stake_type_uid": "STK-LND-WKR",
        "stake_ref_uid": "deed-1", "slot": 2, "asset_kind": "worker_card", "name": "Old 2",
    })
    expanded = _report()
    expanded["generated_at"] = "2026-09-04T06:00:01Z"
    expanded["plots"][0] = _plot("deed-1", powered=False, workers=5)
    expanded["plots"].append(_plot("capacity-peer", powered=True, workers=0))
    expanded["plots"][1]["staking_facts"]["max_workers_allowed"] = 2
    expanded["workers"][0]["slot"] = 1
    expanded["workers"].append({
        "asset_ref": "old-ref-2", "uid": "CARD-OLD-2", "stake_type_uid": "STK-LND-WKR",
        "stake_ref_uid": "deed-1", "slot": 2, "asset_kind": "worker_card", "name": "Old 2",
    })
    for slot in range(3, 6):
        expanded["workers"].append({
            "asset_ref": f"old-ref-{slot}", "uid": f"CARD-OLD-{slot}", "stake_type_uid": "STK-LND-WKR",
            "stake_ref_uid": "deed-1", "slot": slot, "asset_kind": "worker_card", "name": f"Old {slot}",
        })
    monkeypatch.setattr(capture_mod, "read_plots_live", _as_live_read(lambda config, account, deed_uid, *, include_collection: expanded))

    with pytest.raises(PreviewValidationError, match="structure capacity 5"):
        build_preview(
            report,
            [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 5}],
            account="player0005",
            config=object(),
        )


def test_repreview_reads_the_deed_live_every_time(monkeypatch):
    # no reuse cache -- every preview that needs the deed reads it live again.
    report = _report()
    report["generated_at"] = "2026-09-04T06:10:00Z"
    del report["workers"][0]["slot"]
    expanded = _report()
    expanded["generated_at"] = "2026-09-04T06:10:01Z"
    calls = []

    def expand(config, account, deed_uid, *, include_collection):
        calls.append((deed_uid, include_collection))
        return expanded

    monkeypatch.setattr(capture_mod, "read_plots_live", _as_live_read(expand))
    move = [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 1}]

    build_preview(report, move, account="player0005", config=object())
    build_preview(report, move, account="player0005", config=object())
    assert calls == [("deed-1", False), ("deed-1", False)]


def test_missing_worker_count_refuses_after_expansion(monkeypatch):
    report = _report()
    del report["plots"][0]["staking_facts"]["worker_count"]
    monkeypatch.setattr(capture_mod, "read_plots_live", _as_live_read(lambda config, account, deed_uid, *, include_collection: report))
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(
            report,
            [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 1}],
            account="player0005",
            config=object(),
        )

    assert caught.value.errors[0]["notice"]["code"] == "worker_count_unknown"


def test_missing_worker_count_on_source_deed_does_not_permit_capacity_fallback():
    report = _report()
    report["plots"][0]["staking_facts"]["worker_count"] = 0
    source = _plot("source-deed", workers=1)
    del source["staking_facts"]["worker_count"]
    report["plots"].append(source)
    report["workers"][0]["stake_ref_uid"] = "source-deed"

    with pytest.raises(PreviewValidationError) as caught:
        build_preview(
            report,
            [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "old-ref", "slot_index": 0}],
            account="player0005",
        )

    # A worker staked on another deed can no longer move straight across: it must go through the unstake
    # cooldown first, so the move is refused before any capacity is computed -- the unknown source count
    # still never lets a capacity fallback through.
    errors = caught.value.errors
    assert [error["notice"]["code"] for error in errors] == ["cooldown_refusal"]
    assert "currently staked" in errors[0]["reason"]


def test_worker_slot_unknown_and_taken_have_distinct_payload_messages():
    """G3: UNKNOWN and TAKEN remain distinct in both reason code and user-visible message.

    Mutation injection: reuse the occupied-slot notice for the unknown case; the two
    structured notices and their rendered messages must no longer compare equal.
    """
    messages = []
    for slot in (None, 1):
        report = _report()
        if slot is None:
            del report["workers"][0]["slot"]
        try:
            build_preview(report, [{
                "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
                "asset_ref": "new-ref", "slot_index": 0,
            }], account="player0005")
        except PreviewValidationError as caught:
            notice = caught.errors[0]["notice"]
            messages.append((notice["code"], notice["message"]))
    assert messages == [
        ("worker_slot_unknown", "Ceres still does not know which worker slots are occupied. Expand this plot to load it, then try again."),
        ("worker_slot_taken", "The worker slot you chose is already occupied. Choose a different slot."),
    ]
    assert messages[0][1] != messages[1][1]


@pytest.mark.parametrize("roster", ["quick", "empty"])
def test_quick_capture_roster_coverage_refuses_unknown_occupancy(roster):
    """G4: quick-capture-shaped rows and an empty quick roster cannot open the gate.

    The quick row is produced by `normalize_collection_worker`, including its default
    worker stake type and its absent slot. Injection 1: remove that producer default;
    the explicit shape assertion goes red. Injection 2: remove the bulk-count coverage
    refusal; the empty-roster case emits a signable operation and goes red.
    """
    quick_worker = normalize_collection_worker({
        "uid": "QUICK-CARD",
        "card_detail_id": 49,
        "player": "player0005",
        "stake_ref_uid": "deed-1",
        "delegated_to": None,
        "rental_type": None,
    }, observed_at="2026-09-04T00:00:00Z").facts
    assert quick_worker["stake_type_uid"] == "STK-LND-WKR"
    assert quick_worker["stake_ref_uid"] == "deed-1"
    assert "slot" not in quick_worker

    report = _report()
    report["workers"] = [quick_worker] if roster == "quick" else []
    report["plots"][0]["staking_facts"]["worker_count"] = 1 if roster == "quick" else 3
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "new-ref", "slot_index": 0,
        }], account="player0005")
    assert caught.value.errors[0]["notice"]["code"] == ("worker_slot_unknown" if roster == "quick" else "worker_count_mismatch")
    expected_reason = (
        "worker slot occupancy is unknown after expanding the plot; refusing rather than guessing"
        if roster == "quick"
        else "worker roster count does not match the current plot data; refusing rather than guessing"
    )
    assert caught.value.errors[0]["reason"] == expected_reason


def test_runi_is_subtracted_from_bulk_worker_count_before_slot_comparison():
    """G5: a staked Runi contributes to bulk `worker_count` but not numbered worker slots.

    Mutation injection: compare the filtered roster directly with bulk `worker_count`;
    this valid Runi plot would falsely refuse its free numbered slot.
    """
    report = _report()
    report["plots"][0]["staking_facts"].update(is_runi_staked=True, worker_count=2)
    report["workers"].append(_runi_worker("deed-1"))
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref", "slot_index": 1,
    }], account="player0005")
    assert result["operation_count"] == 1


def test_ended_stake_ref_on_real_card_does_not_emit_unstake_for_empty_slot():
    """The fixture is a real collection row whose stake ended before the snapshot.

    Mutation injection: restore unconditional `bool(facts.get("stake_ref_uid"))` trust; the expected one-operation preview goes red.
    """
    report, asset_ref = _stale_stake_report()
    result = build_preview(
        report,
        [{"deed_uid": "target-deed", "group_id": "worker", "action": "stake", "asset_ref": asset_ref, "slot_index": 0}],
        account="player0005",
        now=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    assert result["operation_count"] == 1
    assert result["chunk_count"] == 1
    payload = json.loads(result["chunks"][0]["operations"][0][1]["json"])
    assert payload["unstake"] == {"cards": [], "items": []}
    assert payload["stake"]["cards"] == [{"card_uid": "G-TESTCARD01", "stake_type_uid": "STK-LND-WKR", "slot": "1"}]

    keychain_prompts = []

    def request_broadcast(account, operations, key, callback):
        keychain_prompts.append((account, operations, key))
        callback({"success": True, "result": {"id": "stubbed"}})

    for chunk in result["chunks"]:
        request_broadcast("player0005", chunk["operations"], "Posting", lambda _response: None)
    assert len(keychain_prompts) == 1


def test_candidates_and_preview_agree_that_real_ended_stake_card_is_idle():
    report, asset_ref = _stale_stake_report()
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    candidates = rank_candidates(report, "target-deed", "worker", player="player0005", now=now, limit=10, details_by_id={})
    candidate = next(row for row in candidates["candidates"] if row["asset_ref"] == asset_ref)
    assert candidate["source_deed_uid"] is None

    preview = build_preview(
        report,
        [{"deed_uid": "target-deed", "group_id": "worker", "action": "stake", "asset_ref": asset_ref, "slot_index": 0}],
        account="player0005",
        now=now,
    )
    payload = json.loads(preview["chunks"][0]["operations"][0][1]["json"])
    assert payload["unstake"] == {"cards": [], "items": []}


def test_replace_builds_split_custom_json_operations():
    result = build_preview(_report(), [{
        "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
        "action": "replace", "asset_ref": "new-ref", "evict_asset_ref": "old-ref",
    }], account="player0005")
    assert result["key_tier"] == "Posting"
    assert len(result["chunks"]) == 1
    operations = result["operations"]
    payloads = [json.loads(operation[1]["json"]) for operation in operations]
    assert payloads[0]["unstake"]["cards"] == [{"card_uid": "CARD-OLD"}]
    assert payloads[1]["stake"]["cards"] == [{"card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "1"}]
    assert all(operation[1]["required_auths"] == [] for operation in operations)


def test_title_unstake_is_now_accepted():
    """the internal rules's 2026-08-27 ruling ("titles must not be locked ... behave like all other
    assets") -- `UNVERIFIED_TYPES` in `ceres/op_preview.py` is now empty, so `build_preview`
    must no longer refuse a Title move for being a Title. Inverts the old
    `test_unverified_title_is_rejected`, which this ruling supersedes.

    KNOWN GAP (reported to the dispatching session, not fixed here -- `fides_land/ops.py` is
    outside this agent's `tests/`-only scope): `build_preview` still fails at its final
    `intents_to_ops(...)` call (`ceres/op_preview.py:436`) because `fides_land/ops.py:59`'s
    `_ITEM_STAKE_TYPES` frozenset was never given STAKE_TYPE_TITLE, so `_intent_class` raises
    `LandOpError("unknown stake_type_uid 'STK-LND-TTL'; refusing to emit")` -- a DIFFERENT
    exception, from a DIFFERENT module, than the `UNVERIFIED` refusal this test used to
    check for. This test is therefore currently RED against real master (it still raises,
    just not "UNVERIFIED" any more) -- verified locally by adding STAKE_TYPE_TITLE to
    `_ITEM_STAKE_TYPES`, confirming this test passes end to end, then reverting."""
    report = _report()
    report["bonus_assets"] = [{
        "asset_ref": "title-ref", "uid": "title-uid", "stake_type_uid": "STK-LND-TTL",
        "stake_ref_uid": "deed-1", "asset_kind": "title", "delegated_to": None, "rental_type": None,
    }]
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "title", "slot_index": 0,
        "action": "unstake", "asset_ref": "title-ref",
    }], account="player0005")
    assert result["key_tier"] == "Posting"
    operations = result["operations"]
    payloads = [json.loads(operation[1]["json"]) for operation in operations]
    assert payloads[0]["unstake"]["items"] == [{"item_uid": "title-uid"}]


def _runi_worker(deed_uid: str, *, asset_ref: str = "runi-ref", uid: str = "runi-uid", delegated_to: str | None = "player0005") -> dict:
    """The REAL live shape (board.py:465-467's own comment, corroborated by the live
    snapshot): a staked Runi arrives in `report["workers"]` as `runi_worker_card`, not
    in `bonus_assets`. Crucially it carries a real `delegated_to` -- a Runi is an
    Ethereum NFT delegated to the HIVE account BY CONSTRUCTION, so `delegated_to`
    matching the querying player ("player0005" here, matching `account=` in every call
    below) is the normal, permanent state, not an absent field.  finding 1's
    fixtures instead set `delegated_to: None`, which never exercised the self-
    delegation misread that made every real Runi move fail with a 409."""
    return {
        "asset_ref": asset_ref, "uid": uid, "stake_type_uid": "STK-LND-RUNI",
        "stake_ref_uid": deed_uid, "slot": 0, "asset_kind": "runi_worker_card",
        "card_detail_id": 505, "name": "Runi", "delegated_to": delegated_to, "rental_type": None,
        "market_id": None, "unlock_date": None,
    }


def test_runi_unstake_is_now_accepted_and_emits_bare_card_shape():
    # STK-LND-RUNI's payload shape is now OBSERVED (both directions) -- it must NOT be
    # rejected as UNVERIFIED any more, unlike Titles above.
    report = _report()
    report["plots"][0]["staking_facts"].update({"is_runi_staked": True, "worker_count": 2})
    report["workers"].append(_runi_worker("deed-1"))
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "runi", "slot_index": 0,
        "action": "unstake", "asset_ref": "runi-ref",
    }], account="player0005")
    operations = result["operations"]
    payloads = [json.loads(operation[1]["json"]) for operation in operations]
    assert payloads[0]["unstake"] == {"cards": [{"card_uid": "runi-uid"}], "items": []}
    assert payloads[0]["stake"] == {"cards": [], "items": []}


def test_runi_stake_emits_null_slot_in_cards_shape():
    # No `stake_type_uid` is pre-set on the card's facts here -- a real idle collection
    # card never carries one (op_preview.py's `_asset_record` must infer the class from
    # `card_detail_id` alone, per finding 2).
    report = _report()
    report["cards"].append({
        "asset_ref": "runi-new-ref", "uid": "RUNI-NEW",
        "facts": {  # noqa: keep uid duplicated top-level + facts, matching _report()'s CARD-NEW fixture shape
            "uid": "RUNI-NEW", "player": "player0015", "market_id": None, "delegated_to": "player0005",
            "rental_type": None, "unlock_date": None, "card_detail_id": 505,
        },
    })
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "runi", "slot_index": 0,
        "action": "stake", "asset_ref": "runi-new-ref",
    }], account="player0005")
    operations = result["operations"]
    payloads = [json.loads(operation[1]["json"]) for operation in operations]
    assert payloads[0]["stake"] == {"cards": [{"card_uid": "RUNI-NEW", "stake_type_uid": "STK-LND-RUNI", "slot": None}], "items": []}


def test_ordinary_worker_card_cannot_be_staged_as_runi():
    """ finding 2, the payload-lie bug: before the fix, `_asset_record`'s cards-
    bucket lookup defaulted an unknown `stake_type_uid` to whichever group the CALLER
    asked for (`wanted`) rather than the card's own identity, so an ordinary monster
    card (here `new-ref`, `_report()`'s plain CARD-NEW fixture, no `card_detail_id`
    identifying it as the real Runi 505) could be staged and signed as a Runi. This
    must now be refused with a stake-type mismatch."""
    report = _report()
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "runi", "slot_index": 0,
            "action": "stake", "asset_ref": "new-ref",
        }], account="player0005")
    assert "not 'STK-LND-RUNI'" in str(caught.value.errors)


def test_self_delegated_runi_is_not_refused_as_delegated():
    """ finding 1: every Runi carries `delegated_to` pointing at the querying
    player themselves (the normal, permanent state of a Runi delegation), and the old
    `classify_idle_availability` refused ANY `delegated_to`, misreading delegation-to-
    self as delegation-away. Staging this Runi's unstake must reach a real op, not a
    'delegated to player0005' validation error."""
    report = _report()
    report["plots"][0]["staking_facts"].update({"is_runi_staked": True, "worker_count": 2})
    report["workers"].append(_runi_worker("deed-1", delegated_to="player0005"))
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "runi", "slot_index": 0,
        "action": "unstake", "asset_ref": "runi-ref",
    }], account="player0005")
    assert result["operation_count"] > 0


def test_sole_power_source_hazard_fires_on_runi_removal():
    # The hazard math (`sole_power_source_removed`) was written and unit-tested while
    # Runi staging was still refused -- this exercises it through the real staging path
    # now that the refusal is lifted, per 's instruction to verify it actually fires.
    report = _report()
    report["plots"][0]["staking_facts"]["is_power_core_staked"] = False
    report["plots"][0]["staking_facts"]["is_runi_staked"] = True
    report["plots"][0]["staking_facts"]["worker_count"] = 2
    report["plots"][0]["staking_facts"]["total_harvest_pp"] = 1000
    report["workers"].append(_runi_worker("deed-1"))
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "runi", "slot_index": 0,
        "action": "unstake", "asset_ref": "runi-ref",
    }], account="player0005")
    assert result["hazards"] == [{"type": "sole_power_source_removed", "deed_uid": "deed-1", "message": result["hazards"][0]["message"]}]


def test_chunking_returns_multiple_keychain_arrays():
    report = {"plots": [], "workers": [], "cards": [], "bonus_assets": [], "unassigned_items": {}}
    moves = []
    for index in range(10):
        deed = f"deed-{index}"
        report["plots"].append(_plot(deed, workers=0))
        ref = f"new-{index}"
        report["cards"].append({
            "asset_ref": ref, "uid": f"CARD-{index}",
            "facts": {"uid": f"CARD-{index}", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None},
        })
        moves.append({"deed_uid": deed, "group_id": "worker", "slot_index": 0, "action": "stake", "asset_ref": ref})
    result = build_preview(report, moves, account="player0005")
    assert result["chunk_count"] == 3
    # Ten one-operation moves pack as 4 + 4 + 2; four is deliberate headroom below the chain's
    # hard five-op account-per-block limit because Ceres's broadcast paths are not coordinated.
    assert [chunk["op_count"] for chunk in result["chunks"]] == [4, 4, 2]


@pytest.mark.parametrize("mutator, expected", [
    (lambda report: report["cards"][0]["facts"].update(market_id="listed"), "listed on market"),
    (lambda report: report["cards"][0]["facts"].update(delegated_to="other"), "delegated"),
])
def test_current_snapshot_eligibility_is_rechecked(mutator, expected):
    report = _report()
    mutator(report)
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
            "action": "stake", "asset_ref": "new-ref",
        }], account="player0005")
    assert expected in str(caught.value.errors)


def test_sole_power_source_removal_is_a_hazard_not_a_refusal():
    """Ruling from live multi-transaction testing (2026-08-24): a warning is fine
    but never a blocker. Staging an unstake of a plot's sole power source must
    always be ALLOWED -- Ceres stages only, and Review -> Sign -> Verify is the deliberate
    confirmation the internal rules's "never one-click" rule calls for. `_report()`'s deed-1 carries
    one seated worker, so the hazard fires (there is something at risk); the zero-workers/
    zero-PP no-hazard case is covered in `tests/ceres/test_cooldown_gate.py`."""
    report = _report()
    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "deed-1", "asset_kind": "power_core",
    }]
    result = build_preview(report, [{"deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref"}], account="player0005")
    assert result["hazards"] == [{
        "type": "sole_power_source_removed", "deed_uid": "deed-1",
        "message": "This removes the plot's only power source. All five worker slots remain visible and staked workers remain detectable, but every worker is unpowered, inactive, and produces nothing.",
    }]

    # A solo BLOODLINE holder is no longer refused at unstake either (2026-08-26
    # ruling, supersedes the earlier hard-refusal ruling) -- staging its removal
    # must succeed exactly like any other worker's, `sole_holder_for` notwithstanding.
    report = _report()
    report["workers"][0]["sole_holder_for"] = ["BLOODLINE"]
    result = build_preview(report, [{"deed_uid": "deed-1", "group_id": "worker", "action": "unstake", "asset_ref": "old-ref"}], account="player0005")
    assert result["operation_count"] > 0


def test_orphan_origin_deed_is_a_validation_error_not_a_500():
    """Aletheia finding 5 (2026-08-24): `worker_count_delta`/`core_delta` can be keyed by
    a staked-origin deed pulled straight from the snapshot (an asset's own
    `stake_ref_uid`) that was never checked against `plots` -- only a move's own target
    `deed_uid` is. A snapshot whose `bonus_assets[]` references a `stake_ref_uid` absent
    from `plots[]` (a data-integrity edge case with no known live path, but defended
    anyway) must raise `PreviewValidationError`, never an unhandled `KeyError` -> HTTP
    500. Uses a Power Core (an `items[]` class, no cooldown) rather than a worker card so
    the cooldown gate -- which would otherwise refuse this exact cross-plot move first,
    for an unrelated reason -- doesn't mask the guard actually under test."""
    report = _report()
    report["bonus_assets"] = [{
        "asset_ref": "orphan-core-ref", "uid": "ORPHAN-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "ghost-deed", "asset_kind": "power_core",
        "delegated_to": None, "rental_type": None,
    }]
    with pytest.raises(PreviewValidationError, match="ghost-deed"):
        build_preview(report, [{
            "deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "orphan-core-ref",
        }], account="player0005")


def test_unpowered_workers_are_signable_with_warning_and_same_batch_core_resolves_warning():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"].append(_plot("powered-peer", powered=True, workers=0))
    report["workers"] = []
    report["unassigned_items"] = {"power_core": {"uids": ["core-ref"]}}  # IR-8355bj: the unstaked, owned core this test stages
    worker_move = {"deed_uid": "deed-1", "group_id": "worker", "slot_index": 0, "action": "stake", "asset_ref": "new-ref"}
    warned = build_preview(report, [worker_move], account="player0005")
    notice = next(notice for notice in warned["notices"] if notice["code"] == "worker_unpowered_inactive")
    assert "unpowered and inactive; it produces nothing" in notice["message"]
    assert warned["excluded_moves"] == []

    core_move = {"deed_uid": "deed-1", "group_id": "power_core", "slot_index": 0, "action": "stake", "asset_ref": "core-ref"}
    powered = build_preview(report, [core_move, worker_move], account="player0005")
    assert powered["hazards"] == []
    assert not any(notice["code"] == "worker_unpowered_inactive" for notice in powered["notices"])


def test_first_clear_signs_power_workers_runi_items_and_build_in_one_four_op_transaction():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"].append(_plot("powered-peer", powered=True, workers=0))
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0]["facts"]["terrain"] = "plains"
    report["plots"][0]["facts"]["region"] = "PR-1"
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"] = {
        "power_core": {"uids": ["core-ref"]},
        "totem": {"uids": ["totem-ref"], "items": [{"name": "Common Totem"}]},
        "title": {"uids": ["title-ref"], "items": [{"name": "The Proven"}]},
    }
    report["cards"].append({
        "asset_ref": "runi-ref", "uid": "RUNI-UID",
        "facts": {
            "uid": "RUNI-UID", "name": "Runi Alpha", "player": "player0015",
            "market_id": None, "delegated_to": "player0005", "rental_type": None,
            "unlock_date": None, "card_detail_id": 505,
        },
    })
    moves = [{"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"}]
    for index in range(5):
        ref = "new-ref" if index == 0 else f"new-ref-{index + 1}"
        uid = "CARD-NEW" if index == 0 else f"CARD-NEW-{index + 1}"
        if index:
            report["cards"].append({
                "asset_ref": ref, "uid": uid,
                "facts": {"uid": uid, "name": f"Worker {index + 1}", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None},
            })
        moves.append({"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": ref, "slot_index": index})
    moves.append({"deed_uid": "deed-1", "group_id": "runi", "action": "stake", "asset_ref": "runi-ref"})
    moves.append({"deed_uid": "deed-1", "group_id": "totem", "action": "stake", "asset_ref": "totem-ref"})
    moves.append({"deed_uid": "deed-1", "group_id": "title", "action": "stake", "asset_ref": "title-ref"})
    result = build_preview(report, moves, account="player0005", staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}])
    assert result["operation_count"] == 4
    assert result["chunk_count"] == 1
    assert result["account"] == "player0005"
    assert result["key_tier"] == "Posting"
    assert result["combined_worksite_deeds"] == ["deed-1"]
    assert result["excluded_moves"] == []
    assert not any(notice["code"] == "worker_capacity_refusal" for notice in result["notices"])
    assert result["operations"] == result["chunks"][0]["operations"]
    chunk = result["chunks"][0]
    assert chunk["purpose"] == "first_clear_build"
    assert chunk["op_count"] == 4
    assert chunk["op_count"] <= 4
    assert chunk["stage_groups"] == ["power_core", "worker", "runi", "totem", "title"]
    assert chunk["op_groups"] == ["power_core", ["worker", "runi"], ["totem", "title"], "worksite"]
    assert [operation[1]["id"] for operation in chunk["operations"]] == [
        "sm_stake_change", "sm_stake_change", "sm_stake_change", "sm_land_operation",
    ]
    payloads = [json.loads(operation[1]["json"]) for operation in chunk["operations"]]
    assert payloads[0]["stake"]["items"] == [{"item_uid": "core-ref", "stake_type_uid": "STK-LND-PCR"}]
    assert payloads[0]["stake"]["cards"] == []
    assert len(payloads[1]["stake"]["cards"]) == 6
    assert {card["slot"] for card in payloads[1]["stake"]["cards"]} == {None, "1", "2", "3", "4", "5"}
    assert {card["card_uid"]: card["stake_type_uid"] for card in payloads[1]["stake"]["cards"]}["RUNI-UID"] == "STK-LND-RUNI"
    assert payloads[1]["stake"]["items"] == []
    assert {item["item_uid"]: item["stake_type_uid"] for item in payloads[2]["stake"]["items"]} == {
        "totem-ref": "STK-LND-TOT", "title-ref": "STK-LND-TTL",
    }
    assert payloads[3]["op"] == "worksite_grain_construction"
    assert not any(notice["code"] == "worksite_first_clear_title_unproven" for notice in result["notices"])


def test_first_clear_allows_a_title_without_an_unproven_warning():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"] = {
        "power_core": {"uids": ["core-ref"]},
        "title": {"uids": ["title-ref"], "items": [{"name": "The Proven"}]},
    }
    moves = [
        {"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0},
        {"deed_uid": "deed-1", "group_id": "title", "action": "stake", "asset_ref": "title-ref"},
    ]

    result = build_preview(
        report, moves, account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )

    assert result["chunk_count"] == 1
    assert result["chunks"][0]["op_groups"] == ["power_core", ["worker"], ["title"], "worksite"]
    payload = json.loads(result["chunks"][0]["operations"][2][1]["json"])
    assert payload["stake"]["items"] == [{"item_uid": "title-ref", "stake_type_uid": "STK-LND-TTL"}]
    assert not any(notice["code"] == "worksite_first_clear_title_unproven" for notice in result["notices"])


def test_first_clear_allows_a_runi_as_the_staged_power_source():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["cards"].append({
        "asset_ref": "runi-ref", "uid": "RUNI-UID",
        "facts": {
            "uid": "RUNI-UID", "name": "Runi Alpha", "player": "player0015",
            "market_id": None, "delegated_to": "player0005", "rental_type": None,
            "unlock_date": None, "card_detail_id": 505,
        },
    })
    moves = [
        {"deed_uid": "deed-1", "group_id": "runi", "action": "stake", "asset_ref": "runi-ref"},
    ]

    result = build_preview(
        report, moves, account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )

    assert result["chunk_count"] == 1
    assert result["operation_count"] == 2
    assert result["chunks"][0]["op_groups"] == [["runi"], "worksite"]
    payload = json.loads(result["operations"][0][1]["json"])
    assert payload["stake"]["cards"] == [{
        "card_uid": "RUNI-UID", "stake_type_uid": "STK-LND-RUNI", "slot": None,
    }]


def test_first_clear_combination_refuses_instead_of_splitting_transactions():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"].append(_plot("powered-peer", powered=True, workers=0))
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}
    moves = [
        {"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0},
    ]
    with pytest.raises(LandOpError, match="do not fit in one Hive transaction"):
        build_preview(
            report, moves, account="player0005", max_ops_per_tx=2,
            staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
        )


def test_first_clear_parks_power_core_and_build_without_workers():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}
    result = build_preview(
        report,
        [{"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"}],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )
    assert result["chunk_count"] == 0
    assert "at least one worker" in result["parked_worksites"][0]["reason"]
    assert result["excluded_moves"][0]["asset_ref"] == "core-ref"


def test_first_clear_parks_build_while_same_plot_removal_can_sign():
    report = _report()
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    result = build_preview(
        report,
        [{"deed_uid": "deed-1", "group_id": "worker", "action": "unstake", "asset_ref": "old-ref"}],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )
    assert result["chunk_count"] == 1
    assert result["parked_worksites"]
    assert not result["combined_worksite_deeds"]


def test_first_clear_packs_other_plot_moves_separately():
    report = _report()
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["plots"].append(_plot("deed-2", powered=True, workers=0))
    report["unassigned_items"]["power_core"] = {"uids": ["other-core"]}

    result = build_preview(
        report,
        [{"deed_uid": "deed-2", "group_id": "power_core", "action": "stake", "asset_ref": "other-core"}],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )
    assert result["chunk_count"] == 2
    assert result["combined_worksite_deeds"] == ["deed-1"]
    assert result["chunks"][-1]["purpose"] == "first_clear_build"


def test_unpowered_first_clear_never_signs_workers_without_a_power_source_and_worksite():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    result = build_preview(
        report,
        [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0}],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )
    assert result["chunk_count"] == 0
    assert "Power Core" in result["parked_worksites"][0]["reason"]


def test_live_powered_slot_limit_warns_at_five_and_refuses_only_above_five():
    report = _report()
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 4,
        "worker_count": 2,
        "is_power_core_staked": False,
        "is_energized": False,
        "is_runi_staked": True,
    })
    report["workers"].append(_runi_worker("deed-1"))
    report["cards"].append({
        "asset_ref": "new-ref-2", "uid": "CARD-NEW-2",
        "facts": {"uid": "CARD-NEW-2", "name": "Second Worker", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None},
    })
    result = build_preview(report, [
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 4},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref-2", "slot_index": 5},
    ], account="player0005")
    assert result["operation_count"] == 1
    assert [move["asset_ref"] for move in result["excluded_moves"]] == ["new-ref-2"]
    assert [move["index"] for move in result["excluded_moves"]] == [1]
    warning = next(notice for notice in result["notices"] if notice["code"] == "worker_unpowered_inactive")
    assert "unpowered and inactive; it produces nothing" in warning["message"]
    refusal = next(notice for notice in result["notices"] if notice["code"] == "worker_capacity_refusal")
    assert refusal["reason"].endswith("worker slot 6 exceeds structure capacity 5")
    payload = json.loads(result["operations"][0][1]["json"])
    assert payload["stake"]["cards"] == [{"card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "5"}]


def test_worker_only_unpowered_slot_is_signable_without_a_staged_power_source():
    report = _report()
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 0, "is_power_core_staked": False, "is_energized": False,
    })
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref", "slot_index": 1,
    }], account="player0005")
    assert result["excluded_moves"] == []
    warning = next(notice for notice in result["notices"] if notice["code"] == "worker_unpowered_inactive")
    assert "unpowered and inactive; it produces nothing" in warning["message"]


def test_runi_only_accepts_slot_five_with_warning_and_rejects_slot_six():
    allowed_report = _report()
    allowed_report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 4, "worker_count": 2,
        "is_power_core_staked": False, "is_energized": False, "is_runi_staked": True,
    })
    allowed_report["workers"].append(_runi_worker("deed-1"))
    allowed = build_preview(allowed_report, [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref", "slot_index": 4,
    }], account="player0005")
    assert allowed["excluded_moves"] == []
    assert any(notice["code"] == "worker_unpowered_inactive" for notice in allowed["notices"])

    refused_report = _report()
    refused_report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 4, "worker_count": 2,
        "is_power_core_staked": False, "is_energized": False, "is_runi_staked": True,
    })
    refused_report["workers"].append(_runi_worker("deed-1"))
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(refused_report, [{
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "new-ref", "slot_index": 5,
        }], account="player0005")
    assert caught.value.errors[0]["reason"].endswith("worker slot 6 exceeds structure capacity 5")


def test_out_of_range_replacement_keeps_its_outgoing_worker_staked():
    report = _report()
    report["plots"][0]["staking_facts"]["max_workers_allowed"] = 4
    report["bonus_assets"] = [{
        "asset_ref": "totem-ref", "uid": "ITEM-TOTEM", "stake_type_uid": "STK-LND-TOT",
        "stake_ref_uid": "deed-1", "asset_kind": "totem",
    }]

    result = build_preview(report, [
        {"deed_uid": "deed-1", "group_id": "totem", "action": "unstake", "asset_ref": "totem-ref"},
        {
            "deed_uid": "deed-1", "group_id": "worker", "action": "replace",
            "asset_ref": "new-ref", "evict_asset_ref": "old-ref", "slot_index": 5,
        },
    ], account="player0005")

    assert [move["asset_ref"] for move in result["excluded_moves"]] == ["new-ref"]
    refusal = next(notice for notice in result["notices"] if notice["code"] == "worker_capacity_refusal")
    assert refusal["reason"].endswith("worker slot 6 exceeds structure capacity 5")
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("unstake", {}).get("items") == [{"item_uid": "ITEM-TOTEM"}] for payload in payloads)
    assert not any(payload.get("unstake", {}).get("cards") for payload in payloads)
    assert not any(payload.get("stake", {}).get("cards") for payload in payloads)


def test_pure_unstake_from_existing_slot_above_live_capacity_remains_signable():
    report = _report()
    report["plots"][0]["staking_facts"].update({"max_workers_allowed": 4, "worker_count": 1})
    report["workers"][0]["slot"] = 5

    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "unstake", "asset_ref": "old-ref",
    }], account="player0005")

    assert result["excluded_moves"] == []
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("unstake", {}).get("cards") == [{"card_uid": "CARD-OLD"}] for payload in payloads)


def test_core_removal_projects_runi_only_four_and_warns_for_the_fifth_worker():
    report = _report()
    staking = report["plots"][0]["staking_facts"]
    staking.update({
        "max_workers_allowed": 5, "worker_count": 5,
        "is_power_core_staked": True, "is_energized": False, "is_runi_staked": True,
    })
    for slot in range(2, 5):
        report["workers"].append({
            "asset_ref": f"old-ref-{slot}", "uid": f"CARD-OLD-{slot}",
            "stake_type_uid": "STK-LND-WKR", "stake_ref_uid": "deed-1",
            "slot": slot, "asset_kind": "worker_card", "name": f"Old {slot}",
        })
    report["workers"].append(_runi_worker("deed-1"))
    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "deed-1", "asset_kind": "power_core",
    }]

    result = build_preview(report, [
        {"deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref"},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 4},
    ], account="player0005")

    assert result["excluded_moves"] == []
    warning = next(notice for notice in result["notices"] if notice["code"] == "worker_unpowered_inactive")
    assert warning["reason"].endswith("worker slot 5 is beyond the plot's 4 powered slots")
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("stake", {}).get("cards") == [{
        "card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "5",
    }] for payload in payloads)
    assert any(payload.get("unstake", {}).get("items") == [{"item_uid": "ITEM-CORE"}] for payload in payloads)


def test_runi_removal_projects_zero_and_warns_for_a_new_worker():
    report = _report()
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 4, "worker_count": 1,
        "is_power_core_staked": False, "is_energized": False, "is_runi_staked": True,
    })
    report["workers"] = [_runi_worker("deed-1")]

    result = build_preview(report, [
        {"deed_uid": "deed-1", "group_id": "runi", "action": "unstake", "asset_ref": "runi-ref", "slot_index": 0},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0},
    ], account="player0005")

    assert result["excluded_moves"] == []
    warning = next(notice for notice in result["notices"] if notice["code"] == "worker_unpowered_inactive")
    assert warning["reason"].endswith("worker slot 1 is beyond the plot's 0 powered slots")
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("unstake", {}).get("cards") == [{"card_uid": "runi-uid"}] for payload in payloads)
    assert any(payload.get("stake", {}).get("cards") == [{
        "card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "1",
    }] for payload in payloads)


@pytest.mark.parametrize("group_id", ["power_core", "runi"])
def test_cross_deed_power_item_move_projects_source_removal_and_keeps_move(group_id):
    report = _report()
    report["plots"].append(_plot("deed-2", powered=False, workers=0))
    report["plots"][0]["staking_facts"].update({
        "worker_count": 1 if group_id == "power_core" else 2,
        "max_workers_allowed": 5 if group_id == "power_core" else 4,
        "is_power_core_staked": group_id == "power_core",
        "is_energized": False,
        "is_runi_staked": group_id == "runi",
    })
    if group_id == "power_core":
        report["bonus_assets"] = [{
            "asset_ref": "power-ref", "uid": "POWER-UID", "stake_type_uid": "STK-LND-PCR",
            "stake_ref_uid": "deed-1", "asset_kind": "power_core",
        }]
    else:
        report["workers"].append(_runi_worker("deed-1", asset_ref="power-ref", uid="POWER-UID"))

    result = build_preview(report, [
        {"deed_uid": "deed-2", "group_id": group_id, "action": "stake", "asset_ref": "power-ref", "slot_index": 0},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 1},
    ], account="player0005")

    assert result["excluded_moves"] == []
    assert any(notice["code"] == "worker_unpowered_inactive" for notice in result["notices"])
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    collection = "items" if group_id == "power_core" else "cards"
    uid_key = "item_uid" if group_id == "power_core" else "card_uid"
    assert any(payload.get("unstake", {}).get(collection) == [{uid_key: "POWER-UID"}] for payload in payloads)
    assert any(
        any(entry.get(uid_key) == "POWER-UID" for entry in payload.get("stake", {}).get(collection, []))
        for payload in payloads
    )
    assert any(
        any(entry.get("card_uid") == "CARD-NEW" for entry in payload.get("stake", {}).get("cards", []))
        for payload in payloads
    )


def test_cross_deed_energized_move_projects_source_removal_and_keeps_move(monkeypatch):
    report = _report()
    report["plots"].append(_plot("deed-2", powered=False, workers=0))
    report["plots"][0]["staking_facts"].update({
        "worker_count": 1, "max_workers_allowed": 5,
        "is_power_core_staked": False, "is_energized": True, "is_runi_staked": False,
    })
    report["workers"][0].update({"card_detail_id": 900, "level": 1})
    report["cards"].append({"asset_ref": "old-ref", "facts": {"level": 1}})
    details = {900: {"id": 900, "level": 1, "stats": {"land_abilities": [["ENERGIZED"]]}}}
    monkeypatch.setattr(op_preview_mod, "_cooldown_refusal", lambda *args, **kwargs: None)

    result = build_preview(report, [
        {"deed_uid": "deed-2", "group_id": "worker", "action": "stake", "asset_ref": "old-ref", "slot_index": 0},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0},
    ], account="player0005", details_by_id=details)

    assert result["excluded_moves"] == []
    assert any(notice["code"] == "worker_unpowered_inactive" for notice in result["notices"])
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("unstake", {}).get("cards") == [{"card_uid": "CARD-OLD"}] for payload in payloads)
    assert any(
        any(entry.get("card_uid") == "CARD-OLD" for entry in payload.get("stake", {}).get("cards", []))
        for payload in payloads
    )
    assert any(
        any(entry.get("card_uid") == "CARD-NEW" for entry in payload.get("stake", {}).get("cards", []))
        for payload in payloads
    )


def test_energized_replacement_leaves_runi_powering_four_and_warns_for_slot_five():
    report = _report()
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 5, "worker_count": 5,
        "is_power_core_staked": False, "is_energized": True, "is_runi_staked": True,
    })
    report["workers"][0].update({"card_detail_id": 900, "level": 1})
    report["cards"].append({"asset_ref": "old-ref", "facts": {"level": 1}})
    for slot in range(2, 5):
        report["workers"].append({
            "asset_ref": f"old-ref-{slot}", "uid": f"CARD-OLD-{slot}",
            "stake_type_uid": "STK-LND-WKR", "stake_ref_uid": "deed-1",
            "slot": slot, "asset_kind": "worker_card", "name": f"Old {slot}",
        })
    report["workers"].append(_runi_worker("deed-1"))
    report["cards"].append({
        "asset_ref": "extra-ref", "uid": "CARD-EXTRA",
        "facts": {
            "uid": "CARD-EXTRA", "name": "Extra", "player": "player0005", "market_id": None,
            "delegated_to": None, "rental_type": None, "unlock_date": None,
        },
    })
    details = {900: {"id": 900, "stats": {"land_abilities": [["ENERGIZED"]]}}}

    result = build_preview(report, [
        {
            "deed_uid": "deed-1", "group_id": "worker", "action": "replace",
            "asset_ref": "new-ref", "evict_asset_ref": "old-ref", "slot_index": 0,
        },
        {
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "extra-ref", "slot_index": 4,
        },
    ], account="player0005", details_by_id=details)

    assert result["excluded_moves"] == []
    warning = next(notice for notice in result["notices"] if notice["code"] == "worker_unpowered_inactive")
    assert warning["reason"].endswith("worker slot 5 is beyond the plot's 4 powered slots")
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("unstake", {}).get("cards") == [{"card_uid": "CARD-OLD"}] for payload in payloads)
    staked_cards = [card for payload in payloads for card in payload.get("stake", {}).get("cards", [])]
    assert staked_cards == [
        {"card_uid": "CARD-EXTRA", "stake_type_uid": "STK-LND-WKR", "slot": "5"},
        {"card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "1"},
    ]


def test_capacity_exclusion_cannot_orphan_a_shared_worker_unstake():
    report = _report()
    report["plots"].append(_plot("deed-2", workers=0))
    report["plots"][1]["staking_facts"]["max_workers_allowed"] = 0
    report["plots"][1]["staking_facts"]["worker_count"] = 0
    report["cards"].append({
        "asset_ref": "replacement-ref", "uid": "CARD-REPLACEMENT",
        "facts": {"uid": "CARD-REPLACEMENT", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None},
    })
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [
            {
                "deed_uid": "deed-1", "group_id": "worker", "action": "replace",
                "asset_ref": "replacement-ref", "evict_asset_ref": "old-ref", "slot_index": 0,
            },
            {
                "deed_uid": "deed-2", "group_id": "worker", "action": "stake",
                "asset_ref": "old-ref", "slot_index": 0,
            },
        ], account="player0005")
    assert caught.value.errors[-1]["index"] == 1
    assert "used by more than one staged move" in caught.value.errors[-1]["reason"]


def test_staged_worksite_overlay_rejects_unknown_deed_and_illegal_target():
    report = _report()
    report["plots"][0]["facts"]["terrain"] = "plains"
    move = [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref", "slot_index": 1,
    }]
    with pytest.raises(ValueError, match="unknown deed"):
        build_preview(
            report, move, account="player0005",
            staged_worksites=[{"deed_uid": "missing-deed", "target": "grain"}],
        )
    with pytest.raises(ValueError, match="not legal"):
        build_preview(
            report, move, account="player0005",
            staged_worksites=[{"deed_uid": "deed-1", "target": "iron"}],
        )


def test_fixed_staged_first_clear_capacity_keeps_core_worker_and_build_signable():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}
    result = build_preview(
        report,
        [
            {"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"},
            {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 4},
        ],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )
    assert result["operation_count"] == 3
    assert result["chunk_count"] == 1
    assert result["combined_worksite_deeds"] == ["deed-1"]
    assert result["excluded_moves"] == []
    assert not any(notice["code"] == "staged_capacity_uncertain" for notice in result["notices"])
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert [operation[1]["id"] for operation in result["operations"]] == [
        "sm_stake_change", "sm_stake_change", "sm_land_operation",
    ]
    assert payloads[0]["stake"]["items"] == [{"item_uid": "core-ref", "stake_type_uid": "STK-LND-PCR"}]
    assert payloads[1]["stake"]["cards"] == [{"card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "5"}]
    assert not any(notice["code"] == "worksite_first_clear_no_workers" for notice in result["notices"])


def test_uncleared_staged_energized_worker_powers_all_five_slots_and_signs_build():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["cards"][0]["facts"].update({"card_detail_id": 900, "level": 1})
    details = {900: {"id": 900, "stats": {"land_abilities": [["ENERGIZED"]]}}}

    result = build_preview(
        report,
        [{
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "new-ref", "slot_index": 4,
        }],
        account="player0005",
        details_by_id=details,
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )

    assert result["excluded_moves"] == []
    assert result["combined_worksite_deeds"] == ["deed-1"]
    assert not any(notice["code"] == "worker_unpowered_inactive" for notice in result["notices"])
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert any(payload.get("stake", {}).get("cards") == [{
        "card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "5",
    }] for payload in payloads)
    assert "sm_land_operation" in [operation[1]["id"] for operation in result["operations"]]


def test_fixed_staged_first_clear_rejects_slot_six():
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}

    result = build_preview(
        report,
        [
            {"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"},
            {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 5},
        ],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )

    assert {move["asset_ref"] for move in result["excluded_moves"]} == {"new-ref", "core-ref"}
    notice = next(notice for notice in result["notices"] if notice["code"] == "worker_capacity_refusal")
    assert notice["reason"].endswith("worker slot 6 exceeds structure capacity 5")
    assert result["chunk_count"] == 0
    assert result["parked_worksites"]


def test_malformed_first_clear_projection_keeps_five_and_refuses_slot_six(monkeypatch):
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}
    for index in range(2, 7):
        report["cards"].append({
            "asset_ref": f"new-ref-{index}", "uid": f"CARD-NEW-{index}",
            "facts": {
                "uid": f"CARD-NEW-{index}", "name": f"Worker {index}", "player": "player0005",
                "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None,
            },
        })
    moves = [{
        "deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref",
    }]
    moves.extend({
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref" if index == 1 else f"new-ref-{index}", "slot_index": index - 1,
    } for index in range(1, 7))
    def malformed_projection(*args, **kwargs):
        raise ValueError("malformed projection")

    monkeypatch.setattr(op_preview_mod, "_project_staged_plot_state", malformed_projection)

    result = build_preview(
        report, moves, account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )

    assert [move["asset_ref"] for move in result["excluded_moves"]] == ["new-ref-6"]
    capacity_notices = [notice for notice in result["notices"] if "capacity" in notice["code"]]
    assert len(capacity_notices) == 1
    assert all(notice["code"] == "worker_capacity_refusal" for notice in capacity_notices)
    assert not any(notice["code"] == "staged_capacity_uncertain" for notice in result["notices"])
    payloads = [json.loads(operation[1]["json"]) for operation in result["operations"]]
    assert sum(len(payload.get("stake", {}).get("cards", [])) for payload in payloads) == 5


def test_malformed_first_clear_projection_keeps_fixed_five_slots(monkeypatch):
    report = _report()
    report["plots"][0]["facts"].pop("worksite_type")
    report["plots"][0]["facts"].update({"terrain": "plains", "region": "PR-1"})
    report["plots"][0].pop("worksite_facts")
    report["plots"][0]["diagnostics"] = [{"type": "no_worksite"}]
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 4, "worker_count": 0,
        "is_power_core_staked": False, "is_energized": True,
    })
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}

    def malformed_projection(*args, **kwargs):
        raise ValueError("malformed projection")

    monkeypatch.setattr(op_preview_mod, "_project_staged_plot_state", malformed_projection)
    result = build_preview(
        report,
        [
            {"deed_uid": "deed-1", "group_id": "power_core", "action": "stake", "asset_ref": "core-ref"},
            {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 4},
        ],
        account="player0005",
        staged_worksites=[{"deed_uid": "deed-1", "target": "grain"}],
    )

    assert result["excluded_moves"] == []
    assert not any(notice["code"] == "worker_capacity_refusal" for notice in result["notices"])


def test_malformed_ordinary_projection_keeps_positive_live_capacity(monkeypatch):
    report = _report()
    report["plots"][0]["staking_facts"]["max_workers_allowed"] = 4

    def malformed_projection(*args, **kwargs):
        raise ValueError("malformed projection")

    monkeypatch.setattr(op_preview_mod, "_project_staged_plot_state", malformed_projection)
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
        "asset_ref": "new-ref", "slot_index": 1,
    }], account="player0005")

    assert result["excluded_moves"] == []
    payload = json.loads(result["operations"][0][1]["json"])
    assert payload["stake"]["cards"] == [{
        "card_uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR", "slot": "2",
    }]


def test_malformed_power_removal_projection_still_uses_five_structural_slots(monkeypatch):
    report = _report()
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 5, "worker_count": 2,
        "is_power_core_staked": True, "is_energized": False, "is_runi_staked": True,
    })
    report["workers"].append(_runi_worker("deed-1"))
    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "deed-1", "asset_kind": "power_core",
    }]

    def malformed_projection(*args, **kwargs):
        raise ValueError("malformed projection")

    monkeypatch.setattr(op_preview_mod, "_project_staged_plot_state", malformed_projection)
    result = build_preview(report, [
        {"deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref"},
        {"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 1},
    ], account="player0005")

    assert result["excluded_moves"] == []
    assert not any(notice["code"] == "worker_capacity_refusal" for notice in result["notices"])


def test_malformed_unresolved_energized_removal_never_removes_structural_slots(monkeypatch):
    report = _report()
    report["plots"][0]["staking_facts"].update({
        "max_workers_allowed": 5, "worker_count": 1,
        "is_power_core_staked": False, "is_energized": True,
    })

    def malformed_projection(*args, **kwargs):
        raise ValueError("malformed projection")

    monkeypatch.setattr(op_preview_mod, "_project_staged_plot_state", malformed_projection)
    result = build_preview(report, [
        {"deed_uid": "deed-1", "group_id": "worker", "action": "unstake", "asset_ref": "old-ref"},
        {
            "deed_uid": "deed-1", "group_id": "worker", "action": "stake",
            "asset_ref": "new-ref", "slot_index": 0,
        },
    ], account="player0005")

    assert result["excluded_moves"] == []
    assert not any(notice["code"] == "worker_capacity_refusal" for notice in result["notices"])


def test_browser_preview_path_refuses_an_op_the_allowlist_rejects(monkeypatch):
    """FINDING A: `/op-preview` -- the only path Ceres actually signs through -- must run
    the same `fides_land/render.py:allowlist_rules()` the CLI's `emit` path already used,
    not just build ops from `intents_to_ops` and hand them straight to `requests_for_
    broadcast`. Patches `intents_to_ops` itself (rather than relying on some future gap in
    the intent builder) so this test proves the allowlist is WIRED into build_preview,
    independent of whether the intent builder itself stays bug-free."""
    from fides.op import Op

    def bad_intents_to_ops(intents, *, account, app):
        payload = {
            "unstake": {"cards": [], "items": []},
            "stake": {"cards": [{"card_uid": "X", "stake_type_uid": "STK-LND-TTL", "slot": "1"}], "items": []},
            "deed_uid": "deed-1", "auto_buy_grain": False, "app": app, "n": "abcdefghij",
        }
        return [Op(operation_id="sm_stake_change", required_auths=[], required_posting_auths=[account], json_payload=payload)]

    import ceres.op_preview as op_preview_mod
    monkeypatch.setattr(op_preview_mod, "intents_to_ops", bad_intents_to_ops)
    with pytest.raises(op_preview_mod.LandOpError, match="allowlist refused"):
        build_preview(_report(), [{
            "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
            "action": "unstake", "asset_ref": "old-ref",
        }], account="player0005")


def test_browser_preview_path_permits_a_legitimate_op(monkeypatch):
    """Counterpart to the refusal test above: the allowlist call added to `build_preview`
    must not reject the ordinary, real-shaped op the existing suite already exercises."""
    result = build_preview(_report(), [{
        "deed_uid": "deed-1", "group_id": "worker", "slot_index": 0,
        "action": "unstake", "asset_ref": "old-ref",
    }], account="player0005")
    assert result["chunks"][0]["op_count"] == 1


def test_http_preview_contract_and_no_broadcast(monkeypatch, tmp_path):
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report()
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    response = plot_op_preview("deed-1", {
        "moves": [{"group_id": "worker", "action": "unstake", "asset_ref": "old-ref"}],
    }, request)
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["key_tier"] == "Posting"
    assert body["chunks"][0]["operations"][0][0] == "custom_json"
    assert body["chunks"][0]["operations"][0][1]["id"] == "sm_stake_change"


def test_http_preview_passes_snapshot_config_to_builder(monkeypatch, tmp_path):
    """ROUTE GUARD: dropping `config=config` from the builder call must make this red."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    report = _report()
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda snapshot_config, player: (report, object()))
    seen = {}

    def fake_build_preview(report_arg, moves, **kwargs):
        seen.update(kwargs)
        assert kwargs["config"] is config
        return {"operation_count": 0}

    monkeypatch.setattr(routes_op_preview_mod, "build_preview", fake_build_preview)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    response = plot_op_preview("deed-1", {"moves": []}, request)

    assert response.status_code == 200
    assert seen["config"] is config


def test_redundant_core_removal_builds_unstake_with_energized_power():
    report = _report()
    report["plots"][0]["staking_facts"]["is_energized"] = True
    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "deed-1", "asset_kind": "power_core",
    }]
    result = build_preview(report, [{
        "deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref",
    }], account="player0005")
    assert result["hazards"] == []
    assert len(result["operations"]) == 1
    payload = json.loads(result["operations"][0][1]["json"])
    assert payload["unstake"]["items"] == [{"item_uid": "ITEM-CORE"}]

def test_empty_first_clear_plan_parks_without_blocking_other_plot():
    report = _report()
    plot = _plot("empty", powered=False, workers=0)
    plot["facts"].update({"terrain": "plains", "region": "PR-1"})
    plot["facts"].pop("worksite_type")
    plot.pop("worksite_facts")
    plot["diagnostics"] = [{"type": "no_worksite"}]
    report["plots"].append(plot)
    builds = [{"deed_uid": "empty", "target": "grain"}]
    parked = build_preview(report, [], account="player0005", staged_worksites=builds)
    assert parked["chunk_count"] == 0 and parked["parked_worksites"]
    ready = build_preview(report, [{"deed_uid": "deed-1", "group_id": "worker",
        "action": "unstake", "asset_ref": "old-ref"}], account="player0005", staged_worksites=builds)
    assert ready["chunk_count"] == 1 and ready["parked_worksites"]
    assert json.loads(ready["operations"][0][1]["json"])["unstake"]["cards"] == [{"card_uid": "CARD-OLD"}]
