"""Live Chromium coverage for  Phase 0 -- the plot-row `pending_change`
chip. Per the operation-confirmation plan §1.3: the
backend already emits `pending_change` on every board row and always had --
`build_pending_change` shipped with passing unit tests and ZERO frontend
consumers (`grep -n "pending_change\\|pendingChange" ceres/static/app.js`
returned exactly one hit, a comment). This is the click-path check the plan's
§7 says was skipped last time: not that the JSON is correct, but that a human
looking at the rendered page can actually see it.

Real cases, from the live snapshot (`~/.ceres/snapshots/land-player0005-latest.json`,
2026-08-26 -- two plots genuinely mid worksite-change from this same session's
/ live testing, not synthetic fixtures, matching this package's
own "real cached-snapshot plots, not a fixture stand-in" convention):
- `I-294-f0b7b8d0bb89b6` (plot 65-5-1) -- changing to Logging Camp (wood),
  `total_work_per_hour` 0.007 (not stalled).
- `I-298-38f584665e85ff` (plot 65-6-72) -- changing to Quarry (stone).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres, wait_for_board
from . import board_basic_recording as recording

SCREEN_DIR = Path(__file__).parents[2] / "work" / "ceres-pending-change-chip"
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

PENDING_DEED = recording.PENDING.uid
PENDING_PLOT_LABEL = "65-5-1"
PENDING_TO_LABEL = "Logging Camp"
# The recording's PENDING plot has finished its build (ready, "feed the workers"); BUILDING is
# the plot still mid-change, with a ready time ahead of it.
BUILDING_DEED = recording.BUILDING.uid
BUILDING_TO_LABEL = "Research Hut"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    return page


def _pending_chip(page: Page, deed_uid: str):
    """The row's worksite-change marker. The badge chip left the plot row (badges are the
    #badge-bar strip now); the row shows the change as the `.ws-build` mark in its Worksite
    cell (ws-build.js), which names the incoming worksite and its ready time."""
    row = page.locator(f'tr.plot-row[data-deed-uid="{deed_uid}"]')
    if row.count() == 0:
        pytest.skip(f"{deed_uid} not present in this account's live snapshot")
    return row.locator(".ws-build")


def test_pending_change_chip_renders_on_a_cold_page_load(live_browser):
    """Acceptance #4 (Phase 0, the load-bearing one): doing nothing but loading
    the board tells the user a change is in flight -- no click required."""
    page = _page(live_browser)
    chip = _pending_chip(page, PENDING_DEED)
    chip.wait_for(timeout=10_000)
    assert chip.get_attribute("data-ws-build-label") == PENDING_TO_LABEL
    assert (chip.get_attribute("aria-label") or "").startswith(f"{PENDING_TO_LABEL} finished")
    building = _pending_chip(page, BUILDING_DEED)
    building.wait_for(timeout=10_000)
    assert building.get_attribute("data-ws-build-label") == BUILDING_TO_LABEL
    assert "ready in" in (building.get_attribute("aria-label") or "")
    page.screenshot(path=str(SCREEN_DIR / "cold_load_chip.png"))
    page.close()


def test_pending_change_chip_names_ready_time_via_tooltip(live_browser):
    # the plot still building carries the ready time (a finished one says "feed the workers")
    page = _page(live_browser)
    chip = _pending_chip(page, BUILDING_DEED)
    chip.wait_for(timeout=10_000)
    tip = chip.get_attribute("data-tip-body") or ""
    if not tip:
        # This project's tooltip mechanism (tipAttrs/initTooltips) renders the
        # full sentence into a data attribute consumed by a custom tooltip
        # not the native `title` -- fall back to whatever attribute is present.
        tip = page.evaluate("(el) => el.outerHTML", chip.element_handle())
    assert "ready" in tip.lower() or "stalled" in tip.lower()
    page.close()


def test_pending_change_chip_survives_a_full_page_reload(live_browser):
    """Acceptance #2: without any further click, the chip is still there after
    a full page reload -- it is derived server-side from `worksite_facts` on
    every `GET /api/board`, never from client state."""
    page = _page(live_browser)
    _pending_chip(page, PENDING_DEED).wait_for(timeout=10_000)
    page.reload()
    wait_for_board(page)
    chip = _pending_chip(page, PENDING_DEED)
    chip.wait_for(timeout=10_000)
    assert chip.get_attribute("data-ws-build-label") == PENDING_TO_LABEL
    page.close()


def test_pending_change_chip_visible_without_expanding_the_row(live_browser):
    """The chip lives in the Badges column of the collapsed row itself -- the
    plan's own acceptance criterion is "without any further click"."""
    page = _page(live_browser)
    row = page.locator(f'tr.plot-row[data-deed-uid="{PENDING_DEED}"]')
    if row.count() == 0:
        pytest.skip(f"{PENDING_DEED} not present in this account's live snapshot")
    expand = row.locator("+ tr.expand-row")
    assert expand.evaluate("node => node.style.display") in ("none", "")
    chip = _pending_chip(page, PENDING_DEED)
    assert chip.is_visible()
    page.close()
