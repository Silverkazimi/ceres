"""the recorded, sanitised `board-basic` set replays a full board offline (server and page)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ceres import fixture_upstream
from ceres.config import CeresConfig

SET = Path(__file__).resolve().parents[1] / "fixtures" / "upstream" / "board-basic"
PLAYER = "sample01"

pytestmark = pytest.mark.skipif(not (SET / "fixture-manifest.json").is_file(), reason="board-basic set not recorded")


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    browsers = Path.home() / ".cache" / "ms-playwright"  # keep Chromium findable under the fake HOME
    if browsers.is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    for name in (fixture_upstream.ENV, fixture_upstream.OVERLAYS_ENV, fixture_upstream.CACHE_ENV):
        monkeypatch.delenv(name, raising=False)
    yield
    fixture_upstream.uninstall()


def _deed_uids() -> set[str]:
    for path in SET.rglob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        request = entry.get("request") or {}
        if request.get("path") == "/land/deeds" and request.get("query") == f"player={PLAYER}":
            return {d["deed_uid"] for d in entry["body"]["data"]["deeds"]}
    raise AssertionError("bundle not recorded")


def test_quick_capture_replays_with_no_missing_request(tmp_path):
    from ceres import capture

    fixture_upstream.install(SET)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    events = []
    capture._run_capture_body(config, PLAYER, "quick", lambda kind, **kw: events.append(kind))
    assert events[-1] == "done"
    report, _ref = capture.latest_snapshot(config, PLAYER)
    assert {p["deed_uid"] for p in report["plots"]} == _deed_uids()
    assert fixture_upstream.active().missing == []


@pytest.mark.browser
def test_page_draws_the_recorded_board_offline():
    playwright = pytest.importorskip("playwright.sync_api")
    from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres

    with spawn_fixture_ceres(SET) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1536, "height": 864})
            aborted = install_routes(page, SET)
            page.goto(f"{base}/?player={PLAYER}", timeout=15_000)
            page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=60_000)
            rows = page.locator("#plot-tbody > tr.plot-row").count()
            page.screenshot(path=str(Path(__import__("tempfile").gettempdir()) / "ceres-board-basic.png"))
        finally:
            browser.close()
    assert rows == len(_deed_uids())
    assert aborted == []


OVERLAYS = ["finished-construction", "locked-plot", "staged-lock", "rented-card", "soulkeep-card"]


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_each_recorded_overlay_applies_and_replays(tmp_path, monkeypatch, overlay):
    from ceres import capture

    monkeypatch.setenv(fixture_upstream.OVERLAYS_ENV, overlay)
    fixture_upstream.install(SET)
    config = CeresConfig(root=tmp_path / "home")
    config.ensure_dirs()
    events = []
    capture._run_capture_body(config, PLAYER, "quick", lambda kind, **kw: events.append(kind))
    assert events[-1] == "done"
    assert fixture_upstream.active().missing == []
    report, _ref = capture.latest_snapshot(config, PLAYER)
    plots = {p["deed_uid"]: p for p in report["plots"]}
    if overlay == "locked-plot":
        assert plots["I-298-2dd53d9ac60331"]["facts"]["lock_days"] == 3
    if overlay == "staged-lock":
        assert plots["I-298-88644c3286c361"]["facts"]["lock_days"] is None
    if overlay == "finished-construction":
        assert plots["I-300-663387f4067c0c"]["facts"]["is_construction"]
    if overlay in ("rented-card", "soulkeep-card"):
        pool = capture.load_card_pool(dict(report))
        uids = {c.get("uid") for c in pool.get("cards") or []}
        assert {"rented-card": "B14-765-R2WLILVBZ4", "soulkeep-card": "C11-10012-SAMPLE01"}[overlay] in uids
