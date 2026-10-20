""" slice 3: overlays turn one recorded set into named board states (edits + added entries)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ceres import fixture_upstream
from ceres.config import CeresConfig
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

DEEDS = {"host": "vapi.splinterlands.com", "path": "/land/deeds"}
COLLECTION = {"host": "api.splinterlands.com", "path": "/cards/collection/*"}
LOCKABLE = "I-295-eb4b408d045087"  # lock_days is null in the synthetic bundle


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    for name in (fixture_upstream.ENV, fixture_upstream.OVERLAYS_ENV, fixture_upstream.CACHE_ENV):
        monkeypatch.delenv(name, raising=False)
    yield
    fixture_upstream.uninstall()


def _set(tmp_path: Path, overlays: dict[str, dict]) -> Path:
    root = write_set(tmp_path / "set", board_entries())
    (root / "overlays").mkdir()
    for name, body in overlays.items():
        (root / "overlays" / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    return root


def _body(fixtures, host, path, query=""):
    status, _h, data = fixtures.lookup("GET", host, path + (f"?{query}" if query else ""), None)
    assert status == 200
    return json.loads(data)


OVERLAYS = {
    "finished-construction": {"edits": [{"match": DEEDS, "select": "$.data.worksite_details[0]",
                                          "set": {"is_construction": True, "projected_end": {"$now_offset_seconds": -3600}}}]},
    "locked-plot": {"edits": [{"match": DEEDS, "select": f"$.data.deeds[?(@.deed_uid=='{LOCKABLE}')]",
                                "set": {"lock_days": 3, "unlock_date": {"$now_offset_seconds": 172800}}}]},
    "staged-lock": {"edits": [{"match": DEEDS, "select": f"$.data.deeds[?(@.deed_uid=='{LOCKABLE}')]",
                                "set": {"lock_days": None, "unlock_date": None}}]},
    "rented-card": {"edits": [{"match": COLLECTION, "select": "$.cards[0]",
                                "set": {"rental_type": "rental", "delegated_to": SAMPLE, "player": "sample02"}}]},
    "soulkeep-card": {"edits": [{"match": COLLECTION, "select": "$.cards",
                                  "append": {"uid": "C-SOULKEEP-01", "card_detail_id": 999, "edition": 11,
                                             "player": SAMPLE, "level": 1, "xp": 1, "gold": False, "foil": 0}}]},
}


def test_each_named_state_edits_the_recorded_body(tmp_path):
    root = _set(tmp_path, OVERLAYS)
    now = datetime.now(timezone.utc)
    fixtures = fixture_upstream.FixtureSet(root, overlays=list(OVERLAYS))
    deeds = _body(fixtures, "vapi.splinterlands.com", "/land/deeds", f"player={SAMPLE}")["data"]
    ws = deeds["worksite_details"][0]
    assert ws["is_construction"] is True
    assert datetime.fromisoformat(ws["projected_end"].replace("Z", "+00:00")) < now
    plot = next(d for d in deeds["deeds"] if d["deed_uid"] == LOCKABLE)
    assert plot["lock_days"] is None  # staged-lock (applied after locked-plot) leaves it lockable
    cards = _body(fixtures, "api.splinterlands.com", f"/cards/collection/{SAMPLE}")["cards"]
    assert cards[0]["rental_type"] == "rental" and cards[0]["delegated_to"] == SAMPLE
    assert any(c["uid"] == "C-SOULKEEP-01" and c["edition"] == 11 for c in cards)


def test_locked_plot_reaches_the_board(tmp_path, monkeypatch):
    from ceres import capture

    root = _set(tmp_path, {"locked-plot": OVERLAYS["locked-plot"]})
    monkeypatch.setenv(fixture_upstream.OVERLAYS_ENV, "locked-plot")
    fixture_upstream.install(root)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    events = []
    capture._run_capture_body(config, SAMPLE, "quick", lambda kind, **kw: events.append(kind))
    assert events[-1] == "done"
    report, _ref = capture.latest_snapshot(config, SAMPLE)
    facts = next(p for p in report["plots"] if p["deed_uid"] == LOCKABLE)["facts"]
    assert facts["lock_days"] == 3


def test_a_stale_overlay_refuses_the_set(tmp_path):
    root = _set(tmp_path, {"stale": {"edits": [{"match": DEEDS, "select": "$.data.deeds[?(@.deed_uid=='gone')]",
                                               "set": {"lock_days": 1}}]}})
    with pytest.raises(fixture_upstream.FixtureRefusal, match="selects nothing"):
        fixture_upstream.FixtureSet(root, overlays=["stale"])


def test_added_entries_and_duplicate_refusal(tmp_path):
    add = {"request": {"method": "GET", "host": "vapi.splinterlands.com", "path": "/land/deeds/owned/sample01", "query": ""},
           "body": {"status": "success", "data": []}}
    root = _set(tmp_path, {"extra": {"add": [add]}, "dup": {"add": [add, add]}})
    fixtures = fixture_upstream.FixtureSet(root, overlays=["extra"])
    assert _body(fixtures, "vapi.splinterlands.com", "/land/deeds/owned/sample01") == {"status": "success", "data": []}
    with pytest.raises(fixture_upstream.FixtureRefusal, match="already has"):
        fixture_upstream.FixtureSet(root, overlays=["dup"])


def test_unknown_or_unsafe_overlay_names_are_refused(tmp_path):
    root = _set(tmp_path, {})
    with pytest.raises(fixture_upstream.FixtureRefusal):
        fixture_upstream.FixtureSet(root, overlays=["missing"])
    with pytest.raises(fixture_upstream.FixtureRefusal, match="invalid overlay name"):
        fixture_upstream.FixtureSet(root, overlays=["../escape"])


def test_overlay_files_are_not_loaded_as_recordings(tmp_path):
    root = _set(tmp_path, OVERLAYS)
    assert len(fixture_upstream.FixtureSet(root, overlays=[]).entries) == len(board_entries())
