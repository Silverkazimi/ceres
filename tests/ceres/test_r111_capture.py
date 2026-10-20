"""Focused Phase B guards for the honest full-capture contract."""

from __future__ import annotations

import pytest

from ceres import capture
from ceres.config import CeresConfig


@pytest.fixture
def config(tmp_path) -> CeresConfig:
    return CeresConfig(root=tmp_path / ".ceres", player="player0005")


def _report(player: str) -> dict:
    return {
        "capture": {"requests": 3, "cache_hits": 0},
        "generated_at": "2026-09-04T00:00:00Z",
        "plots": [{"deed_uid": "I-1-1", "asset_fetch": {"status": "retrieved"}}],
        "workers": [],
        "cards": [],
        "bonus_assets": [],
        "warnings": [],
        "coverage": {"assets": {"retrieved": 1, "expected": 1, "complete": True}},
        "query": {"player": player},
    }


def test_full_capture_estimate_exposes_truthful_copy_and_cost(monkeypatch):
    """MUTATION INJECTION: restore any of the removed idle-card, bloodline, or
    sole-holder claims, or hardcode ``Can take minutes`` instead of formatting the
    value returned by ``estimate_capture_seconds``; the metadata assertions redden.
    """

    class FakePreflight:
        def preflight_deed_count(self, player):
            return 129

    monkeypatch.setattr(capture, "LandCapture", FakePreflight)
    result = capture.estimate_capture("player0005")

    assert result["estimated_seconds"] == round(capture.estimate_capture_seconds(129), 1)
    # Q9: the Full capture control is gone, so the estimate carries no user copy any more -- only numbers.
    assert result["deeds"] == 129 and result["requests_estimated"] > 0
    assert "label" not in result and "tooltip" not in result
