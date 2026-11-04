"""Retained  owned-card menu captures from the synthetic card-actions fixture."""
from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from ceres.card_browser import card_row
from ceres.fixture_upstream import FixtureSet
from tests.ceres.fixture_live import IMAGE_HOSTS
from tests.ceres.test_header_menu_browser import CARDS, ORIGIN, STATIC

playwright = pytest.importorskip('playwright.sync_api')
pytestmark = pytest.mark.browser
FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'upstream' / 'board-basic'


def _serve(record):
    """Use the  Cards harness, with the four card-actions overlay copies."""
    html = (STATIC / 'cards.html').read_text(encoding='utf-8')
    html = html.replace('{{CERES_VERSION}}', 't').replace('{{CERES_RELEASED_VERSION}}', 't').replace('{{CERES_LOCAL_MARKER}}', '')
    fixtures = FixtureSet(FIXTURE, overlays=['card-actions'])
    status, _headers, body = fixtures.lookup('GET', 'api.splinterlands.com', '/cards/collection/sample01', None)
    assert status == 200
    collection = json.loads(body)['cards']
    lyanna = [card_row(
        {'uid': copy['uid'], 'facts': copy,
         'classification': {'bucket': 'stake_active_unconfirmed' if copy.get('stake_ref_uid') else 'unstaked_apparent'}},
        {27: {'name': 'Lyanna Natura', 'rarity': 4, 'color': 'Green'}}, {}, player='sample01',
        now=datetime.now(timezone.utc),
    ) for copy in collection if copy.get('card_detail_id') == 27 and copy.get('edition') == 1 and copy.get('foil') == 0]
    assert len(lyanna) == 4, 'card-actions fixture must contain four Lyanna Natura copies'
    cards = [*CARDS, *lyanna]

    def handle(route):
        url = urlparse(route.request.url)
        record.append(url.path + (f'?{url.query}' if url.query else ''))
        if url.hostname in IMAGE_HOSTS:
            return route.fulfill(status=200, content_type='image/png', body=SYNTHETIC_CARD_ART)
        if url.netloc != urlparse(ORIGIN).netloc:
            return route.abort()
        if url.path == '/cards':
            return route.fulfill(status=200, content_type='text/html', body=html)
        if url.path == '/':
            return route.fulfill(status=200, content_type='text/html', body='<!doctype html><title>board</title>')
        if url.path.startswith('/static/'):
            file = STATIC / url.path[len('/static/'):]
            if file.is_file():
                kind = 'text/javascript' if file.suffix == '.js' else (mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                return route.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return route.fulfill(status=404, body='')
        if url.path == '/api/cards':
            return route.fulfill(status=200, content_type='application/json', body=json.dumps({'cards': cards}))
        if url.path == '/api/cards/combinables':
            return route.fulfill(status=200, content_type='application/json', body=json.dumps({'groups': [], 'summary': {}}))
        if url.path.startswith('/api/'):
            return route.fulfill(status=404, content_type='application/json', body=json.dumps({'detail': 'not in this fixture'}))
        return route.fulfill(status=404, body='')
    return handle


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


def _open_lyanna(browser, captures, width, height):
    page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
    page.emulate_media(reduced_motion='reduce')
    record = []
    messages = []
    failed_requests = []
    page.on('console', lambda message: messages.append({'type': message.type, 'text': message.text}))
    page.on('pageerror', lambda error: messages.append({'type': 'pageerror', 'text': str(error)}))
    page.on('requestfailed', lambda request: failed_requests.append({'url': request.url, 'failure': request.failure}))
    page.route('**/*', _serve(record))
    page.goto(f'{ORIGIN}/cards?player=sample01&mode=cards')
    try:
        page.wait_for_selector('body[data-view-ready="true"] #cb-grid .cb-tile', timeout=30_000)
    except playwright.TimeoutError:
        state = page.evaluate('''() => ({
          body_data: Object.fromEntries([...document.body.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])),
          grid_inner_html_length: document.querySelector('#cb-grid')?.innerHTML.length ?? null
        })''')
        diagnostic = {**state, 'console': messages[:30], 'failed_requests': failed_requests,
                      'page_url': page.url, 'requests': record}
        path = captures / f'diag-{width}.json'
        path.write_text(json.dumps(diagnostic, indent=2), encoding='utf-8')
        pytest.fail(f'Cards grid did not become ready at {width}px; {state["body_data"]}, grid HTML length {state["grid_inner_html_length"]}; see {path.name}')
    page.locator('#cb-grid .cb-tile[aria-label*="Lyanna Natura"]').click()
    _art_ready(page)
    return page


def test_card_menu_ci_captures():
    output = os.environ.get('CERES_CI_CAPTURES')
    if not output:
        pytest.skip('CERES_CI_CAPTURES is unset')
    captures = Path(output)
    captures.mkdir(parents=True, exist_ok=True)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        desktop = _open_lyanna(browser, captures, 1536, 864)
        assert desktop.locator('#cm-menu-body [data-copy-index]:checked').count() == 0
        desktop.screenshot(path=str(captures / '01-desktop-none-selected.png'), animations='disabled')
        desktop.locator('#cm-menu-body [data-copy-index="0"]').check()
        assert desktop.locator('#cm-menu-body [data-copy-index]:checked').count() == 1
        _art_ready(desktop)
        desktop.screenshot(path=str(captures / '02-desktop-one-selected.png'), animations='disabled')
        desktop.close()
        phone = _open_lyanna(browser, captures, 390, 844)
        phone.locator('#cm-menu-body [data-copy-index="0"]').check()
        assert phone.locator('#cm-menu-body [data-copy-index]:checked').count() == 1
        _art_ready(phone)
        bar = phone.locator('#cm-menu-body .cm-copy-action-panel').bounding_box()
        assert bar and bar['y'] >= 0 and bar['y'] + bar['height'] <= 844
        phone.screenshot(path=str(captures / '03-phone-one-selected.png'), animations='disabled')
        phone.close()
        browser.close()
