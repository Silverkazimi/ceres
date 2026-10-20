from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_pill_is_materially_larger_than_the_toast(live_browser: tuple[Browser, str]) -> None:
    """T15: reverting the pill to the .worksite-banner width/height makes this red."""
    browser, base_url = live_browser
    page: Page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    page.evaluate(
        """
        document.body.insertAdjacentHTML('beforeend', `
 <div id="ceres-confirmation-banner" class="ceres-pill is-confirmed tone-success">
 <div class="ceres-pill-title-row"><strong class="ceres-pill-title">Confirmed in game</strong><button class="ceres-pill-dismiss">✕</button></div>
 <div class="ceres-pill-detail">3 moves applied.</div>
 <div class="ceres-pill-facts"><div class="ceres-pill-fact"><span class="ceres-pill-label">WHERE</span><span class="ceres-pill-value">001-02-013</span></div></div>
 <div class="ceres-pill-transaction"><a href="https://peakd.com/tx/example" target="_blank" rel="noopener noreferrer">View on Hive</a></div>
 </div>
 <div id="ceres-stage-toast" class="stage-toast">Confirmed in game — 3 moves applied.</div>
 `);
        """
    )
    pill_height = page.locator("#ceres-confirmation-banner").bounding_box()["height"]
    toast_height = page.locator("#ceres-stage-toast").bounding_box()["height"]
    assert pill_height >= toast_height * 4
    page.close()
