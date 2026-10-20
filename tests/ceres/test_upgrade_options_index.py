from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from ceres import card_pool_index, capture
from ceres.board import project_worker_upgrade_options
from ceres.worker_upgrade import WorkerUpgradeOptionsError


DEED = "plot-1"
CURRENT = "current-ref"


def _card(ref: str, uid: str | None, bcx: int, detail_id: int = 7, **extra) -> dict:
    facts = {
        "uid": uid,
        "card_detail_id": detail_id,
        "edition": 4,
        "foil": False,
        "bcx": bcx,
        "bcx_max": 10,
        **extra,
    }
    return {"asset_ref": ref, "facts": facts}


def _report(cards: list[dict], workers: list[dict] | None = None, **extra) -> dict:
    return {
        "coverage": {"assets": {"complete": True}, "collection": {"complete": True}},
        "workers": workers or [{
            "asset_ref": CURRENT,
            "uid": "C-CURRENT",
            "stake_ref_uid": DEED,
            "card_detail_id": 7,
            "edition": 4,
            "foil": False,
            "bcx": 3,
        }],
        "cards": cards,
        **extra,
    }


def _legacy(report: dict, deed_uid: str = DEED, asset_ref: str = CURRENT) -> dict:
    from ceres.board import _project_worker_upgrade_options

    return _project_worker_upgrade_options(report, deed_uid, asset_ref, {})


def _safe_new(report: dict, deed_uid: str = DEED, asset_ref: str = CURRENT):
    try:
        return project_worker_upgrade_options(report, deed_uid, asset_ref)
    except WorkerUpgradeOptionsError as exc:
        return (exc.code, str(exc))


def _safe_legacy(report: dict, deed_uid: str = DEED, asset_ref: str = CURRENT):
    try:
        return _legacy(report, deed_uid, asset_ref)
    except WorkerUpgradeOptionsError as exc:
        return (exc.code, str(exc))


def test_index_output_is_byte_identical_to_legacy_for_5000_cards_with_foil_skin_and_levels():
    cards = [_card(CURRENT, "C-CURRENT", 3, skin="splinterstorm", level=2)]
    cards.extend([_card("copy-a", "C-A", 8, skin="splinterstorm", level=4)])
    cards.extend([_card("foil-copy", "C-FOIL", 8, foil=True, skin="gold-skin", level=4)])
    cards.extend(_card(f"distractor-{i}", f"D-{i}", i % 4 + 1, detail_id=100 + i, level=i % 6 + 1) for i in range(4997))
    report = _report(cards)
    card_pool_index.clear_card_pool_index_cache()

    assert project_worker_upgrade_options(report, DEED, CURRENT) == _legacy(report)


@pytest.mark.parametrize("cards", [
    [_card(CURRENT, "C-CURRENT", 3, detail_id=None), _card("copy-a", "C-A", 8, detail_id=None)],
    [],
])
def test_index_output_matches_legacy_for_missing_detail_id_and_empty_pool(cards):
    report = _report(cards)
    card_pool_index.clear_card_pool_index_cache()

    assert _safe_new(report) == _safe_legacy(report)


def test_missing_detail_id_equality_guard_goes_red_when_current_group_is_perturbed():
    report = _report([_card(CURRENT, "C-CURRENT", 3, detail_id=None)])
    card_pool_index.clear_card_pool_index_cache()
    index = card_pool_index.get_card_pool_index(report)
    index.by_asset_ref[CURRENT] = []

    with pytest.raises(AssertionError):
        assert _safe_new(report) == _safe_legacy(report)


def test_empty_pool_equality_guard_goes_red_when_pool_validity_is_perturbed():
    report = _report([])
    card_pool_index.clear_card_pool_index_cache()
    index = card_pool_index.get_card_pool_index(report)
    index.invalid_count = 1

    with pytest.raises(AssertionError):
        assert _safe_new(report) == _safe_legacy(report)


def test_index_grouping_guard_goes_red_when_group_is_perturbed(monkeypatch):
    report = _report([_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 8)])
    card_pool_index.clear_card_pool_index_cache()
    index = card_pool_index.get_card_pool_index(report)
    original_matching = card_pool_index.CardPoolIndex.matching
    monkeypatch.setattr(card_pool_index.CardPoolIndex, "matching", lambda self, key: [])

    with pytest.raises(AssertionError):
        assert _safe_new(report) == _legacy(report)
    assert original_matching is not None


def test_second_worker_on_same_report_builds_one_index(monkeypatch):
    workers = [
        {"asset_ref": "worker-a", "uid": "A", "stake_ref_uid": "plot-a", "card_detail_id": 7, "edition": 4, "foil": False},
        {"asset_ref": "worker-b", "uid": "B", "stake_ref_uid": "plot-b", "card_detail_id": 7, "edition": 4, "foil": False},
    ]
    report = _report([_card("worker-a", "A", 3), _card("worker-b", "B", 4), _card("copy", "C", 8)], workers)
    card_pool_index.clear_card_pool_index_cache()
    builds = 0
    original_init = card_pool_index.CardPoolIndex.__init__

    def counted_init(self, identity, cards):
        nonlocal builds
        builds += 1
        original_init(self, identity, cards)

    monkeypatch.setattr(card_pool_index.CardPoolIndex, "__init__", counted_init)
    project_worker_upgrade_options(report, "plot-a", "worker-a")
    project_worker_upgrade_options(report, "plot-b", "worker-b")

    assert builds == 1


def test_changed_pool_identity_rebuilds_index(monkeypatch, tmp_path):
    pool_path = tmp_path / "cards.json"
    pool_path.write_text("first")
    report = _report([_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 8)], _cards_sidecar_path=str(pool_path))
    card_pool_index.clear_card_pool_index_cache()
    builds = 0
    original_init = card_pool_index.CardPoolIndex.__init__

    def counted_init(self, identity, cards):
        nonlocal builds
        builds += 1
        original_init(self, identity, cards)

    monkeypatch.setattr(card_pool_index.CardPoolIndex, "__init__", counted_init)
    project_worker_upgrade_options(report, DEED, CURRENT)
    pool_path.write_text("second-pool")
    os.utime(pool_path, ns=(pool_path.stat().st_atime_ns, pool_path.stat().st_mtime_ns + 1_000_000))
    report["cards"] = [_card(CURRENT, "C-CURRENT", 3), _card("copy-b", "C-B", 8)]
    project_worker_upgrade_options(report, DEED, CURRENT)

    assert builds == 2


def test_same_stat_different_pool_rebuilds_after_capture_invalidation(tmp_path):
    pool_path = tmp_path / "cards.json"
    pool_path.write_text("first")
    coverage = {"assets": {"complete": True}, "collection": {"complete": True, "fetched_at": "2026-09-08T10:00:00Z"}}
    report = _report(
        [_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 8)],
        query={"player": "redacted"},
        coverage=coverage,
        _cards_sidecar_path=str(pool_path),
    )
    card_pool_index.clear_card_pool_index_cache()
    first = card_pool_index.get_card_pool_index(report)
    original_stat = pool_path.stat()
    pool_path.write_text("other")
    os.utime(pool_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    report["cards"] = [_card(CURRENT, "C-CURRENT", 3), _card("copy-b", "C-B", 8)]
    card_pool_index.invalidate("redacted")
    second = card_pool_index.get_card_pool_index(report)

    assert second is not first
    assert "copy-a" not in second.by_asset_ref
    assert "copy-b" in second.by_asset_ref


def test_capture_pool_seed_invalidates_existing_index(monkeypatch, tmp_path):
    pool_path = tmp_path / "land-redacted-cards-latest.json"
    coverage = {"assets": {"complete": True}, "collection": {"complete": True, "fetched_at": "2026-09-08T10:00:00Z"}}
    report = _report(
        [_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 8)],
        query={"player": "redacted"},
        coverage=coverage,
        _cards_sidecar_path=str(pool_path),
    )
    card_pool_index.clear_card_pool_index_cache()
    card_pool_index.get_card_pool_index(report)
    builds = 0
    original_init = card_pool_index.CardPoolIndex.__init__

    def counted_init(self, identity, cards):
        nonlocal builds
        builds += 1
        original_init(self, identity, cards)

    monkeypatch.setattr(card_pool_index.CardPoolIndex, "__init__", counted_init)
    capture._save_snapshot_and_refresh_cache(report, tmp_path / "snapshot.json")
    report["cards"] = [_card(CURRENT, "C-CURRENT", 3), _card("copy-b", "C-B", 8)]
    rebuilt = card_pool_index.get_card_pool_index(report)

    assert builds == 1
    assert "copy-a" not in rebuilt.by_asset_ref
    assert "copy-b" in rebuilt.by_asset_ref


@pytest.mark.parametrize(("asset_ref", "assets_complete"), [("missing-worker", True), (CURRENT, False)])
def test_invalid_unrelated_pool_row_preserves_legacy_error_order(asset_ref, assets_complete):
    report = _report(
        [_card(CURRENT, "C-CURRENT", 3), object()],
        coverage={"assets": {"complete": assets_complete}, "collection": {"complete": True}},
    )

    expected = "worker_not_found" if asset_ref == "missing-worker" else "assets_incomplete"
    assert _safe_new(report, asset_ref=asset_ref)[0] == expected


def test_same_key_build_is_single_flight(monkeypatch):
    report = _report([_card(CURRENT, "C-CURRENT", 3), _card("copy-a", "C-A", 8)])
    card_pool_index.clear_card_pool_index_cache()
    builds = 0
    lock = threading.Lock()
    original_init = card_pool_index.CardPoolIndex.__init__

    def counted_init(self, identity, cards):
        nonlocal builds
        with lock:
            builds += 1
        time.sleep(0.01)
        original_init(self, identity, cards)

    monkeypatch.setattr(card_pool_index.CardPoolIndex, "__init__", counted_init)
    errors: list[BaseException] = []

    def load() -> None:
        try:
            card_pool_index.get_card_pool_index(report)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=load) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert builds == 1


def test_index_module_starts_with_guard_target():
    assert Path(card_pool_index.__file__).name == "card_pool_index.py"
