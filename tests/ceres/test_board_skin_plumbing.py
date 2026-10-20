from __future__ import annotations

import json
from pathlib import Path

import pytest

from ceres.board import _skin_by_asset_ref, _worker_row, project_board, project_single_plot


REAL_SNAPSHOT = Path.home() / ".ceres" / "snapshots" / "land-player0005-latest.json"


def test_skin_join_uses_top_level_asset_ref_and_worker_rows_always_emit_skin():
    report = {
        "cards": [
            {"asset_ref": "asset-top-level", "facts": {"uid": "facts-uid", "skin": "Fabled"}},
        ],
    }
    worker = {"asset_ref": "asset-top-level", "card_detail_id": 33}
    row = _worker_row(worker, None, _skin_by_asset_ref(report).get(worker["asset_ref"]), "complete", {}, [])
    assert row["skin"] == "Fabled"
    assert _skin_by_asset_ref({"cards": [{"asset_ref": "facts-uid", "facts": {"skin": "wrong"}}]}).get(worker["asset_ref"]) is None


def test_worker_without_skin_emits_none(report, details_by_id):
    board = project_board(report, details_by_id)
    workers = [worker for plot in board["plots"] for worker in plot["workers"]]
    assert workers
    assert any(worker["skin"] is None for worker in workers)


def test_missing_or_malformed_cards_degrade_safely(report, details_by_id):
    for cards in ("missing", None, {}, [None, "not-a-card"], [{"asset_ref": [], "facts": None, "classification": None}]):
        candidate = dict(report)
        if cards == "missing":
            candidate.pop("cards", None)
        else:
            candidate["cards"] = cards
        board = project_board(candidate, details_by_id)
        assert all(worker["skin"] is None for plot in board["plots"] for worker in plot["workers"])
        if board["plots"]:
            detail = project_single_plot(candidate, board["plots"][0]["deed_uid"], details_by_id)
            assert detail is not None
            assert all(worker["skin"] is None for worker in detail["workers"])
        project_board(candidate, details_by_id, lean=True)


@pytest.mark.skipif(not REAL_SNAPSHOT.is_file(), reason="requires the live Ceres snapshot")
def test_real_snapshot_spirit_of_the_forest_skin_flows_through_both_projections():
    report = json.loads(REAL_SNAPSHOT.read_text(encoding="utf-8"))
    details_by_id = {}
    board = project_board(report, details_by_id)
    plot = next(row for row in board["plots"] if row["deed_uid"] == "I-294-99ed4b7a4d170e")
    worker = next(row for row in plot["workers"] if row["asset_ref"] == "3c1715af6e3c3dab25fc879a")
    assert worker["name"] == "Spirit of the Forest"
    assert worker["skin"] == "Fabled"

    detail = project_single_plot(report, "I-294-99ed4b7a4d170e", details_by_id)
    assert detail is not None
    assert next(row for row in detail["workers"] if row["asset_ref"] == "3c1715af6e3c3dab25fc879a")["skin"] == "Fabled"


# The account's plot count is not fixed (the internal rules: 161 on 2026-08-19, 130 on
# 2026-08-20 after a trade. Never hardcode it anywhere; read it live.)
# the same rule applies to any other live-portfolio count. This test used to
# assert a frozen "16 skinned staked workers (10 Fabled, 6 splinterstorm)"
# figure; the portfolio moves (trades, unstakes, re-skins) and the count
# legitimately drifted to 15, breaking the test for a reason that had
# nothing to do with the plumbing under test. Assert the JOIN CONTRACT
# instead of a snapshot-of-the-day count: every skin the live report
# actually carries (via `cards[].facts.skin`) reaches a `workers[]` row
# through `_skin_by_asset_ref`'s top-level `asset_ref` key -- never through
# `facts.uid` -- and nothing is dropped or fabricated along the way.
@pytest.mark.skipif(not REAL_SNAPSHOT.is_file(), reason="requires the live Ceres snapshot")
def test_real_snapshot_skin_join_matches_live_portfolio_exactly():
    report = json.loads(REAL_SNAPSHOT.read_text(encoding="utf-8"))
    skin_by_asset = _skin_by_asset_ref(report)

    # Ground truth, read directly off the raw `cards[]` payload rather than
    # through the join under test -- top-level `asset_ref` is the join key
    # `facts.uid` is a distinct id that must NOT be used (see
    # test_skin_join_uses_top_level_asset_ref_and_worker_rows_always_emit_skin
    # above for the synthetic-data proof that a `facts.uid` match is ignored).
    expected_skin_by_ref: dict[str, str] = {}
    for card in report.get("cards", []) or []:
        if not isinstance(card, dict):
            continue
        ref = card.get("asset_ref")
        facts = card.get("facts")
        skin = facts.get("skin") if isinstance(facts, dict) else None
        if ref is not None and skin is not None:
            expected_skin_by_ref[str(ref)] = skin

    live_workers = report.get("workers", []) or []
    skins = [skin_by_asset.get(worker.get("asset_ref")) for worker in live_workers]
    skinned_count = sum(skin is not None for skin in skins)

    # Whatever the live count is today, it must equal the number of staked
    # workers whose asset_ref actually appears in the ground-truth map
    # never a hardcoded figure.
    expected_count = sum(
        str(worker.get("asset_ref")) in expected_skin_by_ref for worker in live_workers
    )
    assert skinned_count == expected_count

    # Every non-None skin the join produced must be one the raw card data
    # actually claims for that exact asset_ref (no fabrication, no drift
    # onto the wrong worker).
    for worker in live_workers:
        ref = worker.get("asset_ref")
        joined_skin = skin_by_asset.get(ref)
        if joined_skin is not None:
            assert joined_skin == expected_skin_by_ref.get(str(ref))
