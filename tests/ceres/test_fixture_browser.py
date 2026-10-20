""": the real page draws a board from a fixture set, offline end to end (server and browser)."""

from __future__ import annotations

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.browser
def test_page_draws_the_fixture_board_offline(tmp_path):
    root = write_set(tmp_path / "set", board_entries())
    with spawn_fixture_ceres(root) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1536, "height": 864})
            aborted = install_routes(page, root)
            page.goto(f"{base}/?player={SAMPLE}", timeout=15_000)
            page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
            rows = page.locator("#plot-tbody > tr.plot-row").count()
            health = page.evaluate("fetch('/api/health').then(r => r.json())")
        finally:
            browser.close()
    assert rows == len({d["deed_uid"] for d in board_entries()[0]["body"]["data"]["deeds"]})
    assert health["fixture_upstream"] == "mini"
    assert aborted == []  # the browser asked for nothing outside the fixture hosts
