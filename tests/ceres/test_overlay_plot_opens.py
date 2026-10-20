""": every plot an overlay changes can also be OPENED offline, and the open shows the overlay's state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ceres import fixture_upstream
from ceres.config import CeresConfig
from tests.ceres import board_basic_recording as recording

SET = recording.SET_DIR
PLAYER = recording.PLAYER
PLOTS = {"locked-plot": recording.LOCKED.uid, "staged-lock": recording.STAGED_LOCK.uid,
         "finished-construction": recording.NEVER_BUILT.uid}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    for name in (fixture_upstream.ENV, fixture_upstream.OVERLAYS_ENV, fixture_upstream.CACHE_ENV):
        monkeypatch.delenv(name, raising=False)
    yield
    fixture_upstream.uninstall()


def test_every_overlay_names_a_plot_the_set_can_open():
    uids = set()
    for name in PLOTS:
        text = (SET / "overlays" / f"{name}.json").read_text(encoding="utf-8")
        uids.update(u for u in PLOTS.values() if u in text)
    recorded = {json.loads(p.read_text())["request"]["path"].split("/")[4]
                for p in (SET / "vapi.splinterlands.com" / "GET").glob("*.json")
                if json.loads(p.read_text())["request"]["path"].endswith("/assets")}
    assert uids <= recorded, uids - recorded


@pytest.mark.parametrize("overlay,deed", sorted(PLOTS.items()))
def test_overlay_plot_opens_offline_with_the_overlay_state(tmp_path, monkeypatch, overlay, deed):
    from ceres import capture

    monkeypatch.setenv(fixture_upstream.OVERLAYS_ENV, overlay)
    fixture_upstream.install(SET)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    capture._run_capture_body(config, PLAYER, "quick", lambda kind, **kw: None)
    row = capture.expand_plot(config, PLAYER, deed)
    assert fixture_upstream.active().missing == []
    facts = (row.get("plot") or row).get("facts") if isinstance(row, dict) else None
    if facts is None:
        report, _ref = capture.latest_snapshot(config, PLAYER)
        facts = next(p for p in report["plots"] if p["deed_uid"] == deed)["facts"]
    if overlay == "locked-plot":
        assert facts["lock_days"] == 3
    if overlay == "staged-lock":
        assert facts["lock_days"] is None
