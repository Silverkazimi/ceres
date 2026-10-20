"""all eligible copy locations, and current-worker safety gates."""
from ceres.combine_chain import input_ready, live_ready_at, plan_ready_at, upgrade_inputs
from ceres.worker_upgrade import project_worker_upgrade_options


def card(uid, **extra):
    return {"uid": uid, "asset_ref": uid, "card_detail_id": 7, "edition": 4,
            "foil": False, "bcx": 2, "bcx_max": 10, "stake_type_uid": "STK-LND-WKR", **extra}


def test_free_cooling_staked_and_blocked_inputs():
    rows = [card("C-A"), card("C-B", stake_ref_uid="plot-2"),
            card("C-C", stake_ref_uid="plot-3", stake_end_date="2099-01-01T00:00:00Z"),
            card("C-D", delegated_by="other"), card("C-E", card_locked=True)]
    result = upgrade_inputs(rows, "C-A", account="alice", sources_by_uid={"C-B": {"deed_uid": "plot-2", "slot_index": 3}})
    assert [row["kind"] for row in result[:3]] == ["free", "staked", "cooling"]
    assert result[1]["source"] == {"deed_uid": "plot-2", "slot_index": 3}
    assert result[2]["ready_at"] is None
    assert upgrade_inputs(rows, "C-A", account="alice", verdicts_by_uid={"C-C": {"ready_at": "2099-01-01T00:00:00Z"}})[2]["ready_at"] == "2099-01-01T00:00:00+00:00"
    assert result[3]["eligible"] is False
    assert result[4]["reasons"] == ["This card has a card lock, so it is not included yet."]


def test_ready_time_only_from_live_verdict_and_last_input():
    assert live_ready_at(None) is None
    assert live_ready_at({"signed_at": "2026-10-01T00:00:00Z"}) is None
    assert input_ready({"kind": "cooling"}, {"ready_at": "2026-10-01T00:00:00Z"}) is False
    assert input_ready({"kind": "cooling"}, {"ready": True}) is True
    assert input_ready({"kind": "free"}) is True
    assert plan_ready_at([{"status": "active", "ready_at": None}, {"status": "active", "ready_at": "2026-10-02T00:00:00Z"}]) is None
    assert plan_ready_at([{"status": "active", "ready_at": "2026-10-03T00:00:00Z"}, {"status": "active", "ready_at": "2026-10-02T00:00:00Z"}]) == "2026-10-03T00:00:00Z"


def test_current_staked_worker_is_not_exempt_from_rental_block():
    current = card("C-A", stake_ref_uid="plot-1", rental_type="rent")
    other = card("C-B")
    report = {"coverage": {"assets": {"complete": True}, "collection": {"complete": True}},
              "workers": [current], "cards": [current, other]}
    result = project_worker_upgrade_options(report, "plot-1", "C-A")
    assert result["eligible"] is False
    assert "card is rented" in result["copies"][0]["reasons"]


def test_picker_count_uses_captured_printing_with_staked_copy():
    from ceres.candidates import rank_candidates
    from tests.ceres.test_candidates_api import NOW, _report

    report = _report(powered=True)
    for uid, stake in (("C-A", None), ("C-B", None), ("C-C", "plot-api")):
        report["cards"].append({
            "asset_ref": uid,
            "classification": {"bucket": "assigned_active" if stake else "unstaked_apparent"},
            "facts": card(uid, land_base_pp=1000, level=1, stake_ref_uid=stake),
        })
    details = {7: {"id": 7, "name": "Ordinary", "color": "Red", "rarity": 1}}
    body = rank_candidates(report, "plot-api", "worker", player="alice", now=NOW, details_by_id=details)
    assert {row["upgrade"]["usable_copies"] for row in body["candidates"]} == {3}
