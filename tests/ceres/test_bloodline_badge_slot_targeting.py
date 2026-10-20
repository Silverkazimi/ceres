"""Live Chromium coverage for Bloodline companion picker slot identity.

The fixture uses the current real Ceres board, including a SOLO holder on a high
worker slot and plots with both sparse and full worker grids.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Locator, Page

from .conftest import spawn_live_ceres
from . import board_basic_recording as recording


DEED_UID = recording.RUNI.uid
SCREEN_DIR = Path(__file__).parents[2] / "work" / "ceres-bloodline-badge-slot"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    # The plot count is not fixed -- it changes whenever a plot is traded (129 as
    # measured, 130 before, 161 earlier still), so waiting on a literal count makes
    # the test fail on an unrelated trade rather than on the behaviour under test.
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=30_000,
    )
    return page


def _expand(page: Page, deed_uid: str):
    row = page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']")
    if row.count() == 0:
        pytest.skip(f"{deed_uid} not present in this account's live snapshot")
    expand = row.locator("+ tr.expand-row")
    if expand.evaluate("node => node.style.display") == "none":
        row.click()
    expand.locator(".slot-editor").wait_for(timeout=30_000)
    return expand


def _picker_slot(picker: Locator) -> int:
    context = picker.locator(".picker-context").inner_text()
    match = re.search(r"slot (\d+)", context, flags=re.IGNORECASE)
    assert match, f"picker context has no observed slot index: {context!r}"
    return int(match.group(1)) - 1


def _solo_badge(expand):
    badges = expand.locator(".slot-group[data-group-id='worker'] .slot-tile[data-occupied='1'] button.slot-bloodline-badge.is-solo")
    if badges.count() == 0:
        pytest.skip("the live fixture has no actionable SOLO Bloodline badge")
    badge = badges.first
    tile = badge.locator("xpath=ancestor::*[@data-slot-index][1]")
    return badge, tile


def _make_full_live_fixture(page: Page, expand, badge, tile):
    clicked_name = tile.locator(".slot-name").inner_text()
    page.route(
        "**/api/plots/*/candidates*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"candidates":[{"asset_ref":"fixture-companion","card_uid":"fixture-companion","name":"Fixture Worker","card_name":"Fixture Worker","base_pp":100,"harvest_pp":100,"land_abilities":[],"badges":[],"editions":"2","edition":2,"plot_impact":{"status":"point","value":0,"low":0,"high":0},"construction_pp_delta":100,"cooldown":null,"source_deed_uid":null}],"total":1,"source":"fixture","excluded_counts":{},"notices":[]}',
        ),
    )
    empty = expand.locator(".slot-group[data-group-id='worker'] .slot-tile.is-empty").first
    empty.click()
    picker = expand.locator(".inline-picker")
    picker.wait_for(timeout=30_000)
    picker.locator(".candidate-row").first.click()
    page.wait_for_function(
        "() => document.querySelectorAll(\".slot-group[data-group-id='worker'] .slot-tile.is-empty\").length === 0",
        timeout=30_000,
    )
    full_tile = expand.locator(f".slot-tile[data-slot-index='{tile.get_attribute('data-slot-index')}']")
    full_badge = full_tile.locator("button.slot-bloodline-badge.is-solo")
    if full_badge.count() == 0:
        pytest.skip("the fixture candidate unexpectedly paired the SOLO holder")
    return int(tile.get_attribute("data-slot-index")), clicked_name


def test_solo_badge_opens_picker_on_clicked_slot(live_browser: tuple[Browser, str]) -> None:
    """Injected: restored the `findIndex` empty-slot scan in openBloodlinePairPicker — red, picker opened on slot 1 while slot 4 was clicked (the plan's illustrative slot-0 result does not match this live fixture)."""
    page = _page(live_browser)
    expand = _expand(page, DEED_UID)
    badge, tile = _solo_badge(expand)
    clicked_slot = int(tile.get_attribute("data-slot-index"))
    assert clicked_slot >= 2, "fixture SOLO holder must be on a high worker slot"

    badge.click()
    picker = expand.locator(".inline-picker")
    picker.wait_for(timeout=30_000)
    assert _picker_slot(picker) == clicked_slot
    assert picker.locator("input[data-picker-filter='bloodline_pairs']").is_checked()
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / "solo-badge-picker-1920x1080.png"))
    page.close()


def test_full_plot_badge_replaces_clicked_occupant(live_browser: tuple[Browser, str]) -> None:
    """A full worker grid keeps replacement anchored to the clicked tile's observed slot and card."""
    page = _page(live_browser)
    expand = _expand(page, DEED_UID)
    badge, tile = _solo_badge(expand)
    clicked_slot, clicked_name = _make_full_live_fixture(page, expand, badge, tile)
    full_tile = expand.locator(f".slot-tile[data-slot-index='{clicked_slot}']")
    badge = full_tile.locator("button.slot-bloodline-badge.is-solo")
    badge.click()
    picker = expand.locator(".inline-picker")
    picker.wait_for(timeout=30_000)
    assert _picker_slot(picker) == clicked_slot
    assert picker.locator(".picker-replacing").inner_text().startswith(f"Replacing {clicked_name}")
    page.close()


def test_full_plot_footer_notice_never_selects_occupied_slot(live_browser: tuple[Browser, str]) -> None:
    """The footer fallback is exercised against a real full worker row and has no occupied-slot fallback."""
    page = _page(live_browser)
    expand = _expand(page, DEED_UID)
    badge, tile = _solo_badge(expand)
    _make_full_live_fixture(page, expand, badge, tile)
    deed_uid = expand.locator(".slot-editor-deed").get_attribute("title").split(": ", 1)[1]
    footer = expand.locator(".slot-editor-footer")
    footer.evaluate(
        """(element, deedUid) => element.insertAdjacentHTML(
 'afterbegin',
 `<button class="notice-action" data-notice-action="open-bloodline-pairs" data-deed-uid="${deedUid}">Find a matching companion</button>`,
 )""",
        deed_uid,
    )
    footer.locator("[data-notice-action='open-bloodline-pairs']").click()
    assert expand.locator(".inline-picker").count() == 0
    assert page.locator("#ceres-stage-toast").inner_text() == "All worker slots are full — open a worker tile to swap in a Bloodline companion."
    page.close()
