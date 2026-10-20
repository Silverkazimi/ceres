from __future__ import annotations

from ceres import capture as capture_mod
from ceres.config import CeresConfig


class _RecordingCapture:
    def __init__(self) -> None:
        self.queued: list[list[str]] = []

    def repair_assets(self, deed_uids, player):
        self.queued.append(list(deed_uids))
        return {"plots": []}


def _report(plot_count: int) -> dict:
    return {
        "plots": [{"deed_uid": f"deed-{i}", "asset_fetch": {"status": "not_requested"}} for i in range(plot_count)],
        "coverage": {"assets": {"needs_refetch": []}},
        "warnings": [],
    }


def _run(tmp_path, monkeypatch, plot_count: int) -> _RecordingCapture:
    capture = _RecordingCapture()
    monkeypatch.setattr(capture_mod, "_publish_asset_repair", lambda config, player, prior, incoming, queued: prior)
    monkeypatch.setattr(capture_mod, "_save_merged", lambda config, player, report: report)
    capture_mod._self_heal_refetches(CeresConfig(root=tmp_path / "ceres"), "whale", _report(plot_count), capture=capture)
    return capture


def test_above_1000_plots_skips_background_detail_batch(tmp_path, monkeypatch):
    """a whale's never-fetched plots stay on demand -- no background batch after the board is published."""
    capture = _run(tmp_path, monkeypatch, 1001)
    assert capture.queued == []


def test_ordinary_account_is_on_demand_too(tmp_path, monkeypatch):
    """ (his ruling: "on demand for every account"): a 129-plot account gets no background batch either."""
    capture = _run(tmp_path, monkeypatch, 129)
    assert capture.queued == []


def test_above_1000_plots_keeps_retries(tmp_path, monkeypatch):
    capture = _RecordingCapture()
    report = _report(1001)
    report["plots"][0]["asset_fetch"] = {"status": "failed", "reason": "timeout"}
    report["coverage"]["assets"]["needs_refetch"] = ["deed-0"]
    monkeypatch.setattr(capture_mod, "_publish_asset_repair", lambda config, player, prior, incoming, queued: prior)
    capture_mod._self_heal_refetches(CeresConfig(root=tmp_path / "ceres"), "whale", report, capture=capture)
    assert capture.queued == [["deed-0"]]
