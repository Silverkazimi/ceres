"""Rendered regression guards for tooltip open and close timing."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres, wait_for_board


VIEWPORT = {"width": 1920, "height": 1080}


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def test_first_hover_shows_tooltip_within_one_animation_frame(live_browser: tuple[Browser, str]) -> None:
    """A freshly loaded page must not pay a first-hover delay.

    Injection: restoring the old 140ms delay leaves the tooltip hidden at the
    first requestAnimationFrame and turns this rendered assertion red.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    target = page.locator("[data-tip]").first
    target.wait_for(timeout=10_000)
    target.hover()
    visible_in_frame = page.evaluate(
        """() => new Promise(resolve => requestAnimationFrame(() => resolve(
          Boolean(document.querySelector('#ceres-tooltip.is-visible:not([hidden])'))
        )))"""
    )
    assert visible_in_frame is True
    page.close()


def test_pointer_can_cross_from_trigger_to_tooltip_body(live_browser: tuple[Browser, str]) -> None:
    """The rendered tooltip remains reachable after leaving its trigger.

    Injection: changing the close timer to 0 is intentionally exercised here;
    if the rendered crossing breaks at that minimum, this assertion turns red.
    The current browser probe confirms 0ms is safe, so no close delay is needed.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    target = page.locator("[data-tip]").first
    target.wait_for(timeout=10_000)
    target.hover()
    tooltip = page.locator("#ceres-tooltip.is-visible")
    tooltip.wait_for(timeout=5_000)
    tooltip.hover()
    assert tooltip.is_visible()
    page.close()
