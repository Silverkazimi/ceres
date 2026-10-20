from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ceres import capture as capture_mod
from ceres.app import create_app
from ceres.board import project_worker_upgrade_options
from ceres.config import CeresConfig
from ceres.routes_board import get_worker_upgrade_options
from ceres.worker_upgrade import WorkerUpgradeOptionsError


DEED = "plot-1"
CURRENT = "current-ref"


def _card(ref: str, uid: str | None, bcx: int, *, max_bcx: int | None = 10, **extra) -> dict:
    facts = {
        "uid": uid,
        "card_detail_id": 7,
        "edition": 4,
        "foil": False,
        "bcx": bcx,
        "bcx_max": max_bcx,
        **extra,
    }
    return {"asset_ref": ref, "facts": facts}


def _report(cards: list[dict], *, assets_complete: bool = True, collection_complete: bool = True) -> dict:
    return {
        "coverage": {
            "assets": {"complete": assets_complete},
            "collection": {"complete": collection_complete},
        },
        "workers": [{
            "asset_ref": CURRENT,
            "uid": "C-CURRENT",
            "stake_ref_uid": DEED,
            "card_detail_id": 7,
            "edition": 4,
            "foil": False,
            "bcx": 3,
        }],
        "cards": cards,
    }


def test_projection_counts_only_other_eligible_copies_and_predicts_by_bcx_then_card_id():
    cards = [
        _card("copy-z", "C-Z", 3),
        _card(CURRENT, "C-CURRENT", 3),
        _card("copy-high", "C-HIGH", 8),
    ]

    result = project_worker_upgrade_options(_report(cards), DEED, CURRENT)

    assert result["eligible"] is True
    assert result["combinable_count"] == 2
    assert len(result["other_copies"]) == 2
    assert all(not copy["is_current"] for copy in result["other_copies"])
    assert result["predicted_survivor_uid"] == "C-HIGH"


def test_projection_tie_break_ignores_collection_order_and_unknown_cap_is_not_maxed():
    cards = [
        _card("copy-z", "C-Z", 3, max_bcx=None),
        _card(CURRENT, "C-CURRENT", 3, max_bcx=None),
        _card("copy-a", "C-A", 3, max_bcx=None),
    ]

    result = project_worker_upgrade_options(_report(list(reversed(cards))), DEED, CURRENT)

    assert result["eligible"] is True
    assert result["combinable_count"] == 2
    assert result["predicted_survivor_uid"] == "C-A"
    assert all(copy["bcx_max"] is None for copy in result["copies"])


def test_projection_preserves_skin_on_current_and_other_copies():
    cards = [
        _card(CURRENT, "C-CURRENT", 3, skin="splinterstorm"),
        _card("copy-a", "C-A", 8, skin="splinterstorm"),
    ]

    result = project_worker_upgrade_options(_report(cards), DEED, CURRENT)

    by_ref = {copy["asset_ref"]: copy for copy in result["copies"]}
    assert by_ref[CURRENT]["skin"] == "splinterstorm"
    assert by_ref["copy-a"]["skin"] == "splinterstorm"


def test_maxed_current_worker_is_suppressed():
    """Mutation: removing the current-worker maxed guard made eligible become True; the assertion went red before restoration."""
    result = project_worker_upgrade_options(
        _report([_card(CURRENT, "C-CURRENT", 10), _card("copy-a", "C-A", 1)]), DEED, CURRENT,
    )

    assert result["eligible"] is False
    assert result["combinable_count"] == 0
    assert result["predicted_survivor_uid"] is None
    assert any(reason["reason"] == "current worker is already maxed" for reason in result["reasons"])


def test_maxed_worker_guard_mutation_goes_red(monkeypatch):
    """Injection: disabling `_maxed` made the observable affordance eligibility assertion red; restore it afterward."""
    original = __import__("ceres.worker_upgrade", fromlist=["_maxed"])._maxed
    monkeypatch.setattr("ceres.worker_upgrade._maxed", lambda record, bcx, bcx_max: False)
    result = project_worker_upgrade_options(
        _report([_card(CURRENT, "C-CURRENT", 10), _card("copy-a", "C-A", 1)]), DEED, CURRENT,
    )
    with pytest.raises(AssertionError):
        assert result["eligible"] is False
    assert original is not None


@pytest.mark.parametrize("field", ["assets", "collection"])
def test_incomplete_coverage_is_rejected(field: str):
    """Mutation: accepting incomplete coverage made this test return upgrade options from a partial snapshot; the coverage guard went red before restoration."""
    kwargs = {"assets_complete": True, "collection_complete": True}
    kwargs[f"{field}_complete"] = False

    with pytest.raises(WorkerUpgradeOptionsError) as raised:
        project_worker_upgrade_options(
            _report([_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 1)], **kwargs), DEED, CURRENT,
        )

    assert raised.value.code == f"{field}_incomplete"


def test_only_explicit_unblocked_unmaxed_copy_is_combinable():
    cards = [
        _card(CURRENT, "C-CURRENT", 3),
        _card("missing-id", None, 1),
        _card("staked-copy", "C-STAKED", 1, stake_ref_uid="other-plot"),
        _card("maxed-copy", "C-MAXED", 10),
        _card("usable-copy", "C-USABLE", 2),
    ]

    result = project_worker_upgrade_options(_report(cards), DEED, CURRENT)

    assert result["combinable_count"] == 1
    assert {copy["asset_ref"] for copy in result["other_copies"] if copy["eligible"]} == {"usable-copy"}
    by_ref = {copy["asset_ref"]: copy for copy in result["copies"]}
    assert "Card ID is not present in the snapshot" in by_ref["missing-id"]["reasons"]
    assert by_ref["staked-copy"]["eligible"] is False
    assert by_ref["maxed-copy"]["maxed"] is True


def test_ended_land_stake_does_not_block_worker_upgrade_eligibility():
    """stake_ref_uid persists after a stake ends -- a past stake_end_date must not block.

    Injection run: reverting `_land_stake_reason` to bare
    `if record.get("stake_ref_uid") or ...:` (no stake_end_date check) made this go
    red -- `ended-copy` dropped out of `other_copies` and `combinable_count` fell
    to 0. Restored after observing red.
    """
    cards = [
        _card(CURRENT, "C-CURRENT", 3),
        _card("ended-copy", "C-ENDED", 8, stake_ref_uid="I-294-a2b30476977796", stake_end_date="2026-02-25T00:00:00Z"),
    ]

    result = project_worker_upgrade_options(_report(cards), DEED, CURRENT)

    assert result["combinable_count"] == 1
    assert {copy["asset_ref"] for copy in result["other_copies"] if copy["eligible"]} == {"ended-copy"}
    assert result["predicted_survivor_uid"] == "C-ENDED"


def test_live_land_stake_with_no_end_date_still_blocks_worker_upgrade():
    """A live stake (stake_ref_uid present, stake_end_date unset) must still block.

    Injection run: dropping the `stake_ref_uid` block entirely from
    `_land_stake_reason` made this go red -- `staked-copy` was silently accepted as
    eligible. Restored after observing red.
    """
    cards = [
        _card(CURRENT, "C-CURRENT", 3),
        _card("staked-copy", "C-STAKED2", 8, stake_ref_uid="I-294-5d33fb0c6734d1", stake_end_date=None),
    ]

    result = project_worker_upgrade_options(_report(cards), DEED, CURRENT)

    assert result["combinable_count"] == 0
    by_ref = {copy["asset_ref"]: copy for copy in result["copies"]}
    assert by_ref["staked-copy"]["eligible"] is False
    assert "card is Staked on Land" in by_ref["staked-copy"]["reasons"]


def test_runi_or_item_stake_class_cannot_enter_worker_upgrade_cycle():
    report = _report([_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 1)])
    report["workers"][0]["stake_type_uid"] = "STK-LND-RUNI"
    with pytest.raises(WorkerUpgradeOptionsError, match="only a worker card"):
        project_worker_upgrade_options(report, DEED, CURRENT)


def test_get_route_reads_snapshot_only_and_returns_projection(monkeypatch, tmp_path):
    report = _report([_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 1)])
    config = CeresConfig(root=tmp_path, player="player0005")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})

    response = get_worker_upgrade_options(DEED, CURRENT, request)

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["asset_ref"] == CURRENT
    assert body["combinable_count"] == 1


def test_get_route_is_registered_under_plot_board_routes(tmp_path):
    app = create_app(CeresConfig(root=tmp_path, player="player0005"))

    assert "/api/plots/{deed_uid}/workers/{asset_ref}/upgrade-options" in app.openapi()["paths"]
