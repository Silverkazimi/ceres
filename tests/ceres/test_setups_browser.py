""" phase 2a browser acceptance for public CI only. Do not run on the laptop."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

playwright = pytest.importorskip('playwright.sync_api')
pytestmark = pytest.mark.browser
PLAN_KEY = f'ceres:board-plans:v2:{SAMPLE}'
STAGE_KEY = f'ceres:stage-plan:v1:{SAMPLE}'


def _entry(page, key):
    page.locator('#hdr-menu-btn').click()
    if page.locator('body').get_attribute('data-board-mode') != 'planning':
        assert key == 'planning'
        page.locator('#hdr-menu [data-menu-entry="planning"]').click()
        page.wait_for_function("document.body.dataset.boardMode === 'planning'")
        return
    page.locator('#hdr-menu [data-menu-entry="planning"]').click()
    page.locator('[data-menu-submenu]').wait_for(state='visible')
    if key == 'planning':
        page.locator('[data-menu-submenu] [data-menu-entry="setup-back"]').click()
    elif key == 'setup-panel':
        return
    else:
        page.locator(f'[data-menu-submenu] [data-menu-entry="{key}"]').click()


def _saved(page):
    return page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', PLAN_KEY)


def _selected(page):
    data = _saved(page)
    return data['plans'][data['selected_plan_id']]


def _board_ready(page):
    page.wait_for_selector('#plot-tbody tr.plot-row')


def test_setup_crud_reload_and_live_round_trip(tmp_path):
    fixture = write_set(tmp_path / 'setups', board_entries())
    with spawn_fixture_ceres(fixture) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        install_routes(page, fixture)
        page.goto(f'{base}/?player={SAMPLE}')
        _board_ready(page)
        live_html = page.locator('#plot-tbody').inner_html()
        stage_before = page.evaluate('(key) => localStorage.getItem(key)', STAGE_KEY)
        _entry(page, 'planning')
        page.wait_for_function("document.body.dataset.boardMode === 'planning'")
        initial = _saved(page)
        assert initial['schema_version'] == 2 and len(initial['plans']) == 1
        _entry(page, 'setup-new')
        page.wait_for_function("document.body.dataset.boardPlanName === 'New setup'")
        created = _saved(page)['selected_plan_id']
        _entry(page, 'setup-panel')
        page.locator(f'[data-menu-submenu] [data-setup-rename="{created}"]').click()
        rename = page.locator('#hdr-menu .hdr-menu-rename')
        assert rename.is_visible() and rename.input_value() == 'New setup'
        rename.fill('Grain setup')
        rename.press('Enter')
        page.wait_for_function("document.body.dataset.boardPlanName === 'Grain setup'")
        assert _selected(page)['name'] == 'Grain setup'
        _entry(page, 'setup-duplicate')
        page.wait_for_function("document.body.dataset.boardPlanName === 'Grain setup copy'")
        duplicate = _saved(page)['selected_plan_id']
        assert duplicate != created and _selected(page)['placements'] == []
        assert 'run_state' not in _selected(page)
        _entry(page, f'setup:{created}')
        page.wait_for_function("document.body.dataset.boardPlanName === 'Grain setup'")
        assert _saved(page)['selected_plan_id'] == created
        _entry(page, 'setup-panel')
        page.locator(f'[data-menu-submenu] [data-setup-delete="{created}"]').click()
        page.wait_for_function('(key) => !!JSON.parse(localStorage.getItem(key)).tombstones[%s]' % json.dumps(created), arg=PLAN_KEY)
        assert created not in _saved(page)['plans']
        page.locator('#ceres-stage-toast .stage-toast-undo').click()
        page.wait_for_function('(key) => !!JSON.parse(localStorage.getItem(key)).plans[%s]' % json.dumps(created), arg=PLAN_KEY)
        assert created not in _saved(page)['tombstones']
        _entry(page, 'planning')  # Live
        page.wait_for_function("document.body.dataset.boardMode !== 'planning'")
        assert page.locator('#plot-tbody').inner_html() == live_html
        assert page.evaluate('(key) => localStorage.getItem(key)', STAGE_KEY) == stage_before
        page.reload()
        _board_ready(page)
        _entry(page, 'planning')
        page.wait_for_function("document.body.dataset.boardMode === 'planning'")
        assert _saved(page)['selected_plan_id'] == created
        assert _selected(page)['name'] == 'Grain setup'
        browser.close()


def test_start_from_live_and_zero_wallet_network_on_entry(tmp_path):
    fixture = write_set(tmp_path / 'live-copy', board_entries())
    with spawn_fixture_ceres(fixture) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        page.add_init_script("""window.__walletCalls=[]; window.hive_keychain = new Proxy({}, {
          get: (_target, name) => name === 'then' ? undefined : (...args) => window.__walletCalls.push(String(name)) });""")
        install_routes(page, fixture)
        page.goto(f'{base}/?player={SAMPLE}')
        _board_ready(page)
        before = page.locator('#plot-tbody').inner_html()
        deed = page.locator('#plot-tbody tr.plot-row').first.get_attribute('data-deed-uid')
        all_requests = []
        wallet_workers = []
        relay_sockets = []
        page.on('request', lambda request: all_requests.append((request.method.upper(), request.url)))
        page.on('worker', lambda worker: wallet_workers.append(worker.url))
        page.on('websocket', lambda socket: relay_sockets.append(socket.url))
        _entry(page, 'planning')
        page.wait_for_function("document.body.dataset.boardMode === 'planning'")
        api_requests = [(method, urlparse(url).path) for method, url in all_requests if urlparse(url).path.startswith('/api/')]
        assert api_requests == [], api_requests
        assert not [(method, url) for method, url in all_requests if method == 'POST' and (
            urlparse(url).path.startswith(('/api/wallet/', '/api/auth/'))
            or re.search(r'(?:sign|broadcast)', urlparse(url).path, re.I)
        )]
        assert not [url for _, url in all_requests if 'hiveauth-worker' in url.lower() or 'hive-auth.arcange.eu' in url.lower()]
        assert not [url for url in wallet_workers if 'hiveauth' in url.lower()], wallet_workers
        assert not [url for url in relay_sockets if 'hiveauth' in url.lower() or 'hive-auth.arcange.eu' in url.lower()], relay_sockets
        assert page.evaluate('window.__walletCalls') == []
        def from_live(route):
            body = json.loads(route.request.post_data or '{}')
            assert body['capture_generated_at']
            route.fulfill(status=200, content_type='application/json', body=json.dumps({
                'account': SAMPLE, 'generated_at': body['capture_generated_at'],
                'placements': [{'deed_uid': deed, 'group_id': 'worker', 'slot_index': 0,
                                'asset_ref': 'synthetic-card', 'name': 'Fixture Worker',
                                'asset': {'asset_ref': 'synthetic-card', 'name': 'Fixture Worker'}}]}))
        page.route('**/api/board-plans/from-live**', from_live)
        _entry(page, 'setup-live')
        page.wait_for_function("document.body.dataset.boardPlanName === 'From live'")
        copied = _selected(page)['placements']
        assert [(item['deed_uid'], item['group_id'], item['slot_index'], item['asset']['name']) for item in copied] == [
            (deed, 'worker', 0, 'Fixture Worker')]
        _entry(page, 'planning')
        page.wait_for_function("document.body.dataset.boardMode !== 'planning'")
        assert page.locator('#plot-tbody').inner_html() == before
        assert page.evaluate('window.__walletCalls') == []
        browser.close()
