"""Retained  owned-card menu captures from the synthetic card-actions fixture."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres

playwright = pytest.importorskip('playwright.sync_api')
pytestmark = pytest.mark.browser
FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'upstream' / 'board-basic'


def _art_ready(page):
    art = page.locator('#cm-menu-body .cm-owned-art img')
    art.wait_for(state='visible')
    page.wait_for_function("() => { const img = document.querySelector('#cm-menu-body .cm-owned-art img'); return img && img.complete && img.naturalWidth > 0; }")
    box = art.bounding_box()
    assert box and box['width'] > 0 and box['height'] > 0
    assert page.locator('#cm-menu-body .cm-owned-art').is_visible()


def _open_lyanna(browser, base, width, height):
    page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
    page.emulate_media(reduced_motion='reduce')
    # The tile fixture has no bundled bitmap. Permit only the tile's existing game-art CDN
    # through the fixture router so the capture shows Lyanna's actual artwork.
    install_routes(page, FIXTURE, overlays=['card-actions'], images='cdn')
    page.goto(f'{base}/cards?player=sample01&mode=cards')
    # Match the passing My cards page setup: explicitly select cards mode
    # and wait for the app's view-ready marker plus a real grid tile.
    page.wait_for_selector('body[data-view-ready="true"] #cb-grid .cb-tile', timeout=30_000)
    page.locator('#cb-grid .cb-tile[aria-label*="Lyanna Natura"]').click()
    _art_ready(page)
    return page


def test_card_menu_ci_captures():
    output = os.environ.get('CERES_CI_CAPTURES')
    if not output:
        pytest.skip('CERES_CI_CAPTURES is unset')
    captures = Path(output)
    captures.mkdir(parents=True, exist_ok=True)
    with spawn_fixture_ceres(FIXTURE, overlays='card-actions') as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        desktop = _open_lyanna(browser, base, 1536, 864)
        assert desktop.locator('#cm-menu-body [data-copy-index]:checked').count() == 0
        desktop.screenshot(path=str(captures / '01-desktop-none-selected.png'), animations='disabled')
        desktop.locator('#cm-menu-body [data-copy-index="0"]').check()
        assert desktop.locator('#cm-menu-body [data-copy-index]:checked').count() == 1
        _art_ready(desktop)
        desktop.screenshot(path=str(captures / '02-desktop-one-selected.png'), animations='disabled')
        desktop.close()
        phone = _open_lyanna(browser, base, 390, 844)
        phone.locator('#cm-menu-body [data-copy-index="0"]').check()
        assert phone.locator('#cm-menu-body [data-copy-index]:checked').count() == 1
        _art_ready(phone)
        bar = phone.locator('#cm-menu-body .cm-copy-action-panel').bounding_box()
        assert bar and bar['y'] >= 0 and bar['y'] + bar['height'] <= 844
        phone.screenshot(path=str(captures / '03-phone-one-selected.png'), animations='disabled')
        phone.close()
        browser.close()
