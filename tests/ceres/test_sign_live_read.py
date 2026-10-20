"""Sign reads every staged plot live, in memory only (, , ). --
a plot-open patch vanished on the next capture, so the staged cards were missing from the stored board.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import ceres.capture as capture_mod
import ceres.routes_op_preview as route
from ceres.op_preview import PreviewValidationError

from .vm_context import STATIC

READ_PLOTS_LIVE = capture_mod.read_plots_live  # the real reader, taken before conftest's per-test stand-in


def _stored():
    return {
        "generated_at": "2026-09-27T18:00:00Z",
        "plots": [
            {"deed_uid": "deed-a", "facts": {"plot_id": 11}, "asset_fetch": {"status": "not_requested"}},
            {"deed_uid": "deed-b", "facts": {"plot_id": 12}, "asset_fetch": {"status": "not_requested"}},
        ],
        "workers": [{"asset_ref": "elsewhere-ref", "stake_ref_uid": "deed-b"}],
        "bonus_assets": [],
    }


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(stale_after_seconds=3600))))


@pytest.fixture
def wired(monkeypatch):
    stored = _stored()
    seen = {}
    monkeypatch.setattr(route, "player_for_request", lambda request, player: "player0005")
    monkeypatch.setattr(route, "_snapshot", lambda request, player: (request.app.state.ceres_config, stored))
    monkeypatch.setattr(capture_mod, "load_card_pool", lambda report: report)
    monkeypatch.setattr(route, "app_identity", lambda: {})
    monkeypatch.setattr(route, "load_details_by_id", lambda: {})

    def build(report, moves, **kwargs):
        seen["report"] = report
        return {"operations": [], "moves": moves}

    monkeypatch.setattr(route, "build_preview", build)
    return SimpleNamespace(stored=stored, seen=seen)


def test_every_staged_plot_and_the_card_source_plot_are_read_live_before_the_preview(wired, monkeypatch):
    calls = []

    def read_live(config, player, deed_uids, base):
        calls.append(sorted(deed_uids))
        live = json.loads(json.dumps(base))
        live["workers"].append({"asset_ref": "live-ref", "stake_ref_uid": "deed-a"})
        return {"report": live, "failed": [], "read": sorted(deed_uids)}

    monkeypatch.setattr(capture_mod, "read_plots_live", read_live)
    body = {"moves": [{"deed_uid": "deed-a", "group_id": "worker", "action": "stake", "asset_ref": "elsewhere-ref"}],
            "capture_generated_at": "2026-09-27T18:00:00Z"}
    route._preview(None, body, _request(), "player0005")
    assert calls == [["deed-a", "deed-b"]]  # the staged plot and the plot the card leaves
    assert any(w["asset_ref"] == "live-ref" for w in wired.seen["report"]["workers"])  # built from the live read


def test_an_unanswered_live_read_is_a_coded_503_with_no_text(wired, monkeypatch):
    monkeypatch.setattr(capture_mod, "read_plots_live", lambda c, p, d, b: {"report": b, "failed": ["deed-a"], "read": []})
    body = {"moves": [{"deed_uid": "deed-a", "group_id": "worker", "action": "unstake", "asset_ref": "x"}]}
    with pytest.raises(HTTPException) as caught:
        route._preview(None, body, _request(), "player0005")
    assert caught.value.status_code == 503
    assert caught.value.detail == {"code": "live_read_pending"}


def test_a_card_really_gone_after_the_live_read_is_plan_changed_with_no_text(wired, monkeypatch):
    monkeypatch.setattr(capture_mod, "read_plots_live", lambda c, p, d, b: {"report": b, "failed": [], "read": list(d)})

    def build(report, moves, **kwargs):
        raise PreviewValidationError([{"index": 0, "deed_uid": "deed-a", "asset_ref": "x", "code": "asset_absent",
                                       "reason": "outgoing asset_ref 'x' is not present in the current snapshot"}])

    monkeypatch.setattr(route, "build_preview", build)
    body = {"moves": [{"deed_uid": "deed-a", "group_id": "worker", "action": "unstake", "asset_ref": "x"}]}
    with pytest.raises(HTTPException) as caught:
        route._preview(None, body, _request(), "player0005")
    assert caught.value.status_code == 409
    assert caught.value.detail == {"code": "plan_changed", "moves": [0]}


def test_read_plots_live_merges_in_memory_and_writes_nothing(monkeypatch, tmp_path):
    base = _stored()
    live_plot = {"deed_uid": "deed-a", "facts": {"plot_id": 11}, "asset_fetch": {"status": "retrieved"}}
    expanded = {"plots": [live_plot], "workers": [{"asset_ref": "w1", "stake_ref_uid": "deed-a"}], "bonus_assets": []}
    reads = []
    monkeypatch.setattr(capture_mod, "read_plot_live", lambda player, uid, pid: reads.append((uid, pid)) or expanded)
    monkeypatch.setattr(capture_mod, "merge_reports", lambda base_report, incoming, **kw: incoming)
    before = sorted(tmp_path.rglob("*"))
    result = READ_PLOTS_LIVE(SimpleNamespace(stale_after_seconds=3600), "player0005", ["deed-a"], base)
    assert reads == [("deed-a", 11)]
    assert result["failed"] == [] and result["read"] == ["deed-a"]
    assert any(w["asset_ref"] == "w1" for w in result["report"]["workers"])
    assert base == _stored()  # the stored board is never modified
    assert sorted(tmp_path.rglob("*")) == before


def test_read_plots_live_reports_a_plot_without_retrieved_assets_as_failed(monkeypatch):
    expanded = {"plots": [{"deed_uid": "deed-a", "asset_fetch": {"status": "failed"}}], "workers": []}
    monkeypatch.setattr(capture_mod, "read_plot_live", lambda player, uid, pid: expanded)
    result = READ_PLOTS_LIVE(SimpleNamespace(stale_after_seconds=3600), "player0005", ["deed-a", "unknown"], _stored())
    assert result["failed"] == ["deed-a", "unknown"]


def test_the_live_reader_refuses_any_cache():
    store = capture_mod._NoStore()
    with pytest.raises(Exception):
        store.read("land_assets_x", ttl=0)
    with pytest.raises(Exception):
        store.write("land_assets_x", {}, None)


def test_no_preview_reuse_cache_and_no_plot_patch_on_the_preview_path():
    source = (STATIC.parent / "op_preview.py").read_text(encoding="utf-8")
    assert "_preview_expansion_cache" not in source
    body = source.split("def _expand_plot_for_preview(", 1)[1].split("\ndef ", 1)[0]
    assert "read_plots_live" in body and "expand_plot" not in body.replace("_expand_plot_for_preview", "")


def test_page_re_asks_silently_and_marks_moved_cards():
    source = (STATIC / "operations.js").read_text(encoding="utf-8")
    block = source.split("payload?.detail?.code === 'live_read_pending'", 1)[1].split("continue;", 1)[0]
    assert "state.error" not in block and "state.refreshing = true" in block
    assert "[1000, 2000, 4000][liveAsks] ?? 8000" in block
    assert "_plan_invalid_reason = 'card_moved'" in source
