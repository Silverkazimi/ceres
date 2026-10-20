""" (v1.0 plan C1, praetoria side): no built-in account."""

from __future__ import annotations

import pytest

from praetoria import cli, land_analysis
from praetoria.land_analysis import LandCapture


def test_default_player_is_none() -> None:
    assert land_analysis.DEFAULT_PLAYER is None


@pytest.mark.parametrize("call", [
    lambda c: c.preflight_deed_count(),
    lambda c: c.capture(offline=True),
    lambda c: c.capture_quick(),
    lambda c: c.repair_assets([]),
    lambda c: c.expand_deed("I-1-aaaa"),
])
def test_capture_entry_points_refuse_a_missing_player(call) -> None:
    with pytest.raises(ValueError, match="player required"):
        call(LandCapture())


def test_land_cli_requires_player_for_a_live_capture(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["land", "status", "--offline"]) == 7
    assert "--player is required" in capsys.readouterr().err
