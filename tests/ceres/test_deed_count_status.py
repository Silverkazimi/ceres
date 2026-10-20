""": the quick read publishes the plot count and a measured-rate time estimate on
the status payload as soon as the deeds bundle is in (`ceres.capture.record_quick_timing`
/ `estimate_quick_seconds`). The model is seeded from measured reads (`QUICK_TIMING_SEED`)
and persisted to `quick-timing.json` under the Ceres data directory."""

from __future__ import annotations

import json
import math

import pytest

from ceres import capture as capture_mod
from ceres.config import CeresConfig


def _reset_globals(monkeypatch):
    monkeypatch.setattr(capture_mod, "_quick_base_seconds", None)
    monkeypatch.setattr(capture_mod, "_quick_seconds_per_deed", None)
    monkeypatch.setattr(capture_mod, "_quick_timing_samples", 0)
    monkeypatch.setattr(capture_mod, "_quick_timing_loaded", False)
    monkeypatch.setattr(capture_mod, "_quick_timing_players", {})


@pytest.fixture(autouse=True)
def fresh_timing(monkeypatch):
    _reset_globals(monkeypatch)


def _seed_estimate(deed_count: int) -> float:
    seed = capture_mod.QUICK_TIMING_SEED
    return float(math.ceil(max(seed["base_seconds"], seed["seconds_per_deed"] * deed_count)))


def test_status_payload_carries_estimated_seconds():
    payload = capture_mod.CaptureStatus().as_dict()
    assert "estimated_seconds" in payload
    assert payload["estimated_seconds"] is None


def test_estimate_learns_only_from_measurements():
    # Fresh state: the measured seed answers (never None, never an invented constant).
    assert capture_mod.estimate_quick_seconds(500) == _seed_estimate(500)
    assert _seed_estimate(500) == float(math.ceil(max(4.8, 500 * 6.6 / 461)))
    capture_mod.record_quick_timing(100, 4.0)
    small = capture_mod.estimate_quick_seconds(100)
    assert isinstance(small, float) and small >= 4.0
    large = capture_mod.estimate_quick_seconds(1000)
    assert large is not None and large >= small


def test_degenerate_measurements_are_ignored():
    capture_mod.record_quick_timing(0, 5.0)
    capture_mod.record_quick_timing(10, 0.0)
    assert capture_mod.estimate_quick_seconds(100) == _seed_estimate(100)
    assert capture_mod._quick_timing_samples == 0


def test_measurement_persists_across_restart(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path)
    capture_mod.record_quick_timing(100, 4.0, config)
    path = capture_mod._quick_timing_path(config)
    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["base_seconds"] == 4.0
    assert stored["seconds_per_deed"] == pytest.approx(0.04)
    assert stored["samples"] == 1
    assert "updated_at" in stored

    _reset_globals(monkeypatch)  # simulate a restart / deploy
    learned = capture_mod.estimate_quick_seconds(100, config)
    assert learned is not None and learned >= 4.0
    assert learned == float(math.ceil(max(4.0, 0.04 * 100)))
    assert learned != _seed_estimate(100)
    assert capture_mod._quick_timing_samples == 1


def test_corrupt_file_falls_back_to_seed(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path)
    path = capture_mod._quick_timing_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert capture_mod.estimate_quick_seconds(300, config) == _seed_estimate(300)
    assert capture_mod._quick_timing_loaded is True


# (v0.9.376): a player's own last measured read beats the shared model.
def test_player_own_timing_preferred_over_seed():
    capture_mod.record_quick_timing(129, 3.2, player="player0005")
    assert capture_mod.estimate_quick_seconds(129, player="player0005") == pytest.approx(3.2)
    # A second run folds in with alpha 0.5.
    capture_mod.record_quick_timing(129, 4.0, player="player0005")
    assert capture_mod.estimate_quick_seconds(129, player="player0005") == pytest.approx(3.6)
    # Plot count within 10 %: unscaled; beyond: scaled by the ratio.
    assert capture_mod.estimate_quick_seconds(135, player="player0005") == pytest.approx(3.6)
    assert capture_mod.estimate_quick_seconds(258, player="player0005") == pytest.approx(7.2)


def test_unknown_player_uses_shared_model():
    capture_mod.record_quick_timing(129, 3.2, player="player0005")
    assert capture_mod.estimate_quick_seconds(500, player="somebody") == capture_mod.estimate_quick_seconds(500)
    assert capture_mod.estimate_quick_seconds(500, player="somebody") >= 3.2
    assert capture_mod.estimate_quick_seconds(500, player="somebody") == float(math.ceil(max(3.2, 500 * 3.2 / 129)))


def test_players_round_trip_through_file(tmp_path, monkeypatch):
    config = CeresConfig(root=tmp_path)
    capture_mod.record_quick_timing(129, 3.2, config, player="player0005")
    stored = json.loads(capture_mod._quick_timing_path(config).read_text(encoding="utf-8"))
    assert stored["players"]["player0005"]["seconds"] == pytest.approx(3.2)
    assert stored["players"]["player0005"]["deeds"] == 129
    assert "at" in stored["players"]["player0005"]

    _reset_globals(monkeypatch)  # restart
    assert capture_mod.estimate_quick_seconds(129, config, player="player0005") == pytest.approx(3.2)
    assert capture_mod.estimate_quick_seconds(129, config, player="player0001") == float(math.ceil(max(3.2, 129 * 3.2 / 129)))
