"""Retained  owned-card menu captures from the synthetic card-actions fixture."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.ceres.fixture_live import IMAGE_HOSTS, install_routes, spawn_fixture_ceres

playwright = pytest.importorskip('playwright.sync_api')
pytestmark = pytest.mark.browser
FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'upstream' / 'board-basic'
def _synthetic_card_art(width=300, height=420):
    """A plain synthetic card picture built at runtime (no committed image, no game art): a two-colour gradient
    with a lighter centre panel, so the capture shows where the artwork sits and how large it is."""
    import struct, zlib
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            inner = 40 <= x < width - 40 and 60 <= y < height - 120
            r, g, b = (60 + y * 120 // height, 40 + x * 80 // width, 140)
            if inner:
                r, g, b = min(255, r + 70), min(255, g + 70), min(255, b + 70)
            row += bytes((r, g, b))
        rows.append(bytes(row))
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')


SYNTHETIC_CARD_ART = _synthetic_card_art()


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
    # The tile fixture has no bundled bitmap. Serve synthetic art locally for both
    # game image hosts, then install the normal offline fixture routes.
    # Playwright checks the newest matching route first, so this CDN route takes
    # precedence over install_routes' image placeholder handler.
    def serve_synthetic_art(route):
        if (urlsplit(route.request.url).hostname or '') in IMAGE_HOSTS:
            return route.fulfill(status=200, content_type='image/png', body=SYNTHETIC_CARD_ART)
        return route.fallback()

    install_routes(page, FIXTURE, overlays=['card-actions'])
    page.route('**/*', serve_synthetic_art)
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
