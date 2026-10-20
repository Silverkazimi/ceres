""": dropping a plot's outdated worker detail after the maintainer's own change is normal — no bar,
and the worker-detail advisory fires only on a failed read, never counting plots "not read yet"."""
from __future__ import annotations

import json
from types import SimpleNamespace

from ceres import capture as capture_mod
from ceres import routes_board
from ceres.config import CeresConfig
from ceres.notices import format_notice
from tests.ceres.test_plot_demotion import _plot, _report


def _payload(monkeypatch, tmp_path, **coverage):
    report = _report([_plot("a", "retrieved", 0)], [], [])
    report["coverage"]["assets"].update({"expected": 1264, "retrieved": 2, "fresh": 2, "dropped": 0, "failed": 0, "not_requested": 0, **coverage})
    report["warnings"] = []
    config = CeresConfig(root=tmp_path, player="player0008")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))
    monkeypatch.setattr(routes_board, "_read_snapshot_or_503", lambda *_a: (report, SimpleNamespace(generated_at="2099")))
    monkeypatch.setattr(capture_mod, "freshness", lambda *_a: SimpleNamespace(error=None, as_dict=lambda: {}))
    monkeypatch.setattr(routes_board, "load_details_by_id", lambda: {})
    monkeypatch.setattr(
        routes_board,
        "_cached_project_board",
        lambda *_a, **_k: ({"notices": [], "warnings": [], "coverage": report["coverage"]}, None, None, None),
    )
    response = routes_board.get_board(request)
    return json.loads(response.body), json.loads(response.headers["X-Ceres-Board-Status"])


def test_dropped_after_change_raises_no_bar(monkeypatch, tmp_path) -> None:
    payload, status = _payload(monkeypatch, tmp_path, dropped=1, not_requested=1261)
    assert not any(n["code"] == "assets_incomplete" for n in payload["notices"] + status["notices"])
    assert not any(str(w).startswith("STALE DATA") for w in payload.get("warnings", []))


def test_failed_read_raises_one_notice_without_not_read_yet(monkeypatch, tmp_path) -> None:
    payload, status = _payload(monkeypatch, tmp_path, failed=1, dropped=1, not_requested=1261)
    notices = [n for n in payload["notices"] if n["code"] == "assets_incomplete"]
    assert len(notices) == 1 and len([n for n in status["notices"] if n["code"] == "assets_incomplete"]) == 1
    message = notices[0]["message"]
    assert "not read yet" not in message
    assert "1 plot(s) could not be read" in message and "1 dropped after a change" in message
    assert "not read yet" not in format_notice("assets_incomplete", context={"failed": 1, "dropped_clause": ""})["message"]


def test_cross_check_still_drops_stale_rows_as_diagnostic() -> None:
    from tests.ceres.test_stale_board_refresh import REAL_DEED_UID, REAL_WORKERS, _real_plot

    base = {"plots": [_real_plot(6, retrieved=True)], "workers": list(REAL_WORKERS), "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {}}
    incoming = {"plots": [_real_plot(5, retrieved=False)], "workers": [], "cards": [], "bonus_assets": [], "unassigned_items": {}, "coverage": {}}
    merged = capture_mod.merge_reports(base, incoming)
    plot = next(p for p in merged["plots"] if p["deed_uid"] == REAL_DEED_UID)
    assert plot["asset_fetch"]["status"] == "dropped_stale"
    assert merged["coverage"]["assets"]["dropped"] == 1
    assert not any(str(w).startswith("STALE DATA") for w in merged["warnings"])
    diag = [d for d in merged["diagnostics"] if d.get("code") == "worker_detail_dropped_stale"]
    assert len(diag) == 1 and diag[0]["deed_uid"] == REAL_DEED_UID
    assert diag[0]["bulk_worker_count"] == 5 and diag[0]["retrieved_worker_count"] == 6
