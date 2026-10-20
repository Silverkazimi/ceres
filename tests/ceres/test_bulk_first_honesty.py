"""Bulk-first board honesty (2026-08-21 correction): a quick (bulk-only) board load
must never present unfetched worker/ability/idle-card data as a measured zero.
Reuses `tests/ceres/conftest.py`'s fixtures but strips per-deed asset coverage down to
`not_requested`, simulating exactly what `capture_quick()` (no per-deed fan-out) would
have produced -- proving `project_board` exposes the joined roster while leaving
asset-only fields honest."""

from __future__ import annotations

import copy

from ceres.board import project_board


def _strip_asset_coverage(report: dict) -> dict:
    """Simulate a bulk-only (`capture_quick()`) report from the full-coverage fixture:
    no plot has retrieved assets, and the per-deed-sourced `bonus_assets` list is empty.
    The worker roster remains because it is joined from the complete collection."""
    quick = copy.deepcopy(report)
    for plot in quick["plots"]:
        plot["asset_fetch"] = {"status": "not_requested"}
    quick["bonus_assets"] = []
    quick["coverage"] = dict(quick.get("coverage", {}))
    quick["coverage"]["assets"] = {"retrieved": 0, "expected": len(quick["plots"]), "complete": False}
    return quick


def test_quick_board_preserves_loading_state_without_badge(report, details_by_id):
    quick = _strip_asset_coverage(report)
    board = project_board(quick, details_by_id)
    assert all(row["assets_loaded"] is False for row in board["plots"])
    assert all("assets_not_loaded" not in row["badges"] for row in board["plots"])


def test_full_board_has_no_assets_not_loaded_badge(report, details_by_id):
    """Sanity check the badge is genuinely conditional -- the full-coverage fixture
    (every deed's assets retrieved, even if empty) never fires it."""
    board = project_board(report, details_by_id)
    assert all(row["assets_loaded"] is True for row in board["plots"])
    assert all("assets_not_loaded" not in row["badges"] for row in board["plots"])


def test_quick_board_idle_cards_not_loaded(report, details_by_id):
    quick = _strip_asset_coverage(report)
    board = project_board(quick, details_by_id)
    assert board["idle_cards_loaded"] is False
    assert board["idle_cards"] == []


def test_full_board_idle_cards_loaded(report, details_by_id):
    board = project_board(report, details_by_id)
    assert board["idle_cards_loaded"] is True


def test_quick_board_unassigned_runi_and_land_card_not_capturable(report, details_by_id):
    quick = _strip_asset_coverage(report)
    board = project_board(quick, details_by_id)
    unassigned = board["unassigned_bonus"]
    assert unassigned["runi"]["capturable"] is False
    assert unassigned["land_card"]["capturable"] is False
    assert "full capture" in unassigned["runi"]["reason"]


def test_quick_board_totem_title_power_core_still_capturable(report, details_by_id):
    """Source A (totem/title/power_core) is an account-scoped /available call,
    independent of per-deed asset coverage -- must NOT be gated by idle_cards_loaded,
    unlike runi/land_card (Source B, which genuinely does need it)."""
    quick = _strip_asset_coverage(report)
    quick["unassigned_items"] = {
        "totem": {"capturable": True, "count": 5, "items": [{"item_detail_id": 1, "name": "Totem", "count": 5}]},
        "title": {"capturable": True, "count": 0, "items": []},
        "power_core": {"capturable": True, "count": 2, "items": [{"item_detail_id": 2, "name": "Power Core", "count": 2}]},
        "runi": {"capturable": False, "count": None, "items": [], "reason": "P0001 upstream error"},
    }
    board = project_board(quick, details_by_id)
    unassigned = board["unassigned_bonus"]
    assert unassigned["totem"]["capturable"] is True
    assert unassigned["totem"]["count"] == 5
    assert unassigned["power_core"]["capturable"] is True
    # runi/land_card stay gated on idle_cards_loaded regardless of Source A's own state.
    assert unassigned["runi"]["capturable"] is False
    assert unassigned["land_card"]["capturable"] is False


def test_partial_expand_updates_only_that_deed_loading_state(report, details_by_id):
    quick = _strip_asset_coverage(report)
    target = report["plots"][0]["deed_uid"]
    for plot in quick["plots"]:
        if plot["deed_uid"] == target:
            plot["asset_fetch"] = {"status": "retrieved"}
    board = project_board(quick, details_by_id)
    by_uid = {row["deed_uid"]: row for row in board["plots"]}
    assert by_uid[target]["assets_loaded"] is True
    assert "assets_not_loaded" not in by_uid[target]["badges"]
    others = [row for uid, row in by_uid.items() if uid != target]
    assert others  # sanity: fixture has more than one plot
    assert all(row["assets_loaded"] is False for row in others)
    assert all("assets_not_loaded" not in row["badges"] for row in others)
    # idle_cards stays gated -- one deed's worth of coverage isn't the WHOLE account.
    assert board["idle_cards_loaded"] is False


def test_quick_board_derives_totem_identity_and_title_class_from_bulk(report, details_by_id):
    """G04/G05 mutation trial: swapping a ladder entry or naming a 0.50 title exactly
    made this assertion RED; the restored bulk projection is GREEN."""
    quick = _strip_asset_coverage(report)
    source = quick["plots"][0]
    quick["plots"] = []
    cases = (("totem", 0.1, "Common Totem Land", "common"), ("totem", 0.25, "Rare Totem Land", "rare"),
             ("totem", 0.5, "Epic Totem Land", "epic"), ("totem", 1.0, "Legendary Totem Land", "legendary"),
             ("title", 0.1, "Rare Title", "rare"), ("title", 0.25, "Epic Title", "epic"),
             ("title", 0.5, "Legendary Title", "legendary"))
    for index, (kind, boost, name, rarity) in enumerate(cases):
        plot = copy.deepcopy(source)
        plot["deed_uid"] = f"derived-{index}"
        plot["staking_facts"]["totem_boost"] = boost if kind == "totem" else 0
        plot["staking_facts"]["title_boost"] = boost if kind == "title" else 0
        quick["plots"].append(plot)
    board = project_board(quick, details_by_id)
    for index, (kind, _boost, name, rarity) in enumerate(cases):
        value = next(row["bonus"][kind] for row in board["plots"] if row["deed_uid"] == f"derived-{index}")
        assert value["name"] == name
        assert value["rarity"] == rarity
        assert value["resolution"] == "derived"


def test_bulk_asset_mismatch_is_explicit_conflict(report, details_by_id):
    """G08 mutation trial: suppressing the conflict marker made this assertion RED;
    the restored mismatch output is GREEN."""
    quick = _strip_asset_coverage(report)
    target = quick["plots"][0]
    target["staking_facts"]["totem_boost"] = 0.25
    quick["bonus_assets"] = [{"asset_kind": "totem", "name": "Epic Totem", "boost": "0.500", "stake_ref_uid": target["deed_uid"]}]
    board = project_board(quick, details_by_id)
    row = next(row for row in board["plots"] if row["deed_uid"] == target["deed_uid"])
    totem = row["bonus"]["totem"]
    assert totem["name"] == "Epic Totem"
    assert totem["resolution"] == "conflict"
    assert totem["conflict"] == {"bulk_boost": 0.25, "asset_boost": 0.5}
