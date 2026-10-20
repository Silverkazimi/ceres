""" guard for the capture freshness field shared by both lanes."""

from __future__ import annotations

from pathlib import Path

import pytest

from ceres import capture


ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / "ceres" / "capture.py"
# (2026-09-04, ceres v0.9.49): coverage assembly moved from
# routes_board.py into board_artifact.py's prepare_board(), which now owns the
# producer-side field the browser contract is checked against.
BOARD_ARTIFACT = ROOT / "ceres" / "board_artifact.py"
BOARD_JS = ROOT / "ceres" / "static" / "board.js"
FIELD = "oldest_observed_at"
LEGACY_FIELD = "oldest_" + "fetched_at"


def _assert_field_contract(producer_field: str, capture_source: str, artifact_source: str, browser_source: str) -> None:
    assert producer_field == FIELD
    assert f'.get("{FIELD}")' in capture_source
    assert f'"{FIELD}"' in artifact_source
    # R-111 (maintainer-answered 2026-09-03): a surface never shows a figure which is
    # not currently true, so age/staleness (including this field) was removed
    # from the browser entirely rather than kept in sync with a name.
    assert FIELD not in browser_source
    assert LEGACY_FIELD not in capture_source
    assert LEGACY_FIELD not in artifact_source
    assert LEGACY_FIELD not in browser_source


def test_capture_field_contract_and_injected_collision_goes_red() -> None:
    capture_source = CAPTURE.read_text(encoding="utf-8")
    artifact_source = BOARD_ARTIFACT.read_text(encoding="utf-8")
    browser_source = BOARD_JS.read_text(encoding="utf-8")
    producer_report = {FIELD: "2026-09-04T00:00:00Z", "plots": [], "workers": []}

    merged = capture.merge_reports(None, producer_report)
    assert merged[FIELD] == producer_report[FIELD]
    _assert_field_contract(FIELD, capture_source, artifact_source, browser_source)

    injected_browser = LEGACY_FIELD.join(["preamble ", " tail"])
    with pytest.raises(AssertionError):
        _assert_field_contract(FIELD, capture_source, artifact_source, injected_browser)
