from __future__ import annotations

import copy

from ceres.board import project_board
from praetoria.cache import JsonCache
from praetoria.land_analysis import LandCapture
from praetoria.transport import ResponseData
from tests.ceres.test_shared_verification_evidence import _bundle


class _SequenceLandClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.bundle_calls = 0

    def bundle(self, player):
        payload = self.payloads[min(self.bundle_calls, len(self.payloads) - 1)]
        self.bundle_calls += 1
        return ResponseData(copy.deepcopy(payload), "2037-01-01T00:00:01Z", 1)


class _CollectionClient:
    def collection(self, player):
        return ResponseData({"cards": []}, "2037-01-01T00:00:02Z", 1)


def _payload(*, complete: bool):
    payload = _bundle()
    if complete:
        for deed in payload["data"]["deeds"]:
            deed["plot_status"] = "active"
    return payload


def _capture(tmp_path, monkeypatch, payloads):
    land_client = _SequenceLandClient(payloads)
    capture = LandCapture(
        land_client=land_client,
        collection_client=_CollectionClient(),
        cache=JsonCache(tmp_path / "cache"),
        min_delay=0,
        concurrency=1,
    )
    monkeypatch.setattr(capture, "_capture_prior_resources", lambda *args, **kwargs: {})
    monkeypatch.setattr(capture, "_card_names", lambda *args, **kwargs: {})
    monkeypatch.setattr(capture, "_fetch_item_catalogue", lambda *args, **kwargs: [])
    monkeypatch.setattr(capture, "_capture_unassigned_items", lambda *args, **kwargs: {})
    return capture, land_client


def test_incomplete_bundle_is_refetched_once_then_publishes_complete_board(tmp_path, monkeypatch):
    capture, land_client = _capture(tmp_path, monkeypatch, [_payload(complete=False), _payload(complete=True)])

    report = capture.capture_quick("player0005")
    board = project_board(report, lean=True)

    assert land_client.bundle_calls == 2
    assert report["warnings"] == []
    assert all(plot["facts"]["status"] == "active" for plot in report["plots"])
    assert board["notices"] == []


def test_incomplete_bundle_twice_is_refetched_once_and_board_notice_is_present(tmp_path, monkeypatch):
    capture, land_client = _capture(tmp_path, monkeypatch, [_payload(complete=False), _payload(complete=False)])

    report = capture.capture_quick("player0005")
    board = project_board(report, lean=True)

    assert land_client.bundle_calls == 2
    assert any("Some plot data is missing" in warning and "Refresh the board" in warning for warning in report["warnings"])
    assert any("Some plot data is missing" in notice.get("message", "") for notice in board["notices"])


def test_complete_bundle_does_not_refetch(tmp_path, monkeypatch):
    capture, land_client = _capture(tmp_path, monkeypatch, [_payload(complete=True)])

    report = capture.capture_quick("player0005")

    assert land_client.bundle_calls == 1
    assert report["warnings"] == []
