"""Synthetic planning setup screenshots retained by core browser CI."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

playwright = pytest.importorskip('playwright.sync_api')
pytestmark = pytest.mark.browser
PLAN_KEY = f'ceres:board-plans:v2:{SAMPLE}'


def _entry(page, key):
    page.locator('#hdr-menu-btn').click()
    page.wait_for_selector('#hdr-menu:not([hidden])')
    page.locator(f'#hdr-menu [data-menu-entry="{key}"]').click()


def _saved(page):
    return page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', PLAN_KEY)


def _selected(page):
    data = _saved(page)
    return data['plans'][data['selected_plan_id']]


def _menu(page):
    page.locator('#hdr-menu-btn').click()
    page.wait_for_selector('#hdr-menu:not([hidden])')


def _capture(page, folder, name):
    page.screenshot(path=str(folder / name), full_page=False, animations='disabled')


def test_planning_setup_ci_captures(tmp_path):
    output = os.environ.get('CERES_CI_CAPTURES')
    if not output:
        pytest.skip('CERES_CI_CAPTURES is unset')
    captures = Path(output)
    captures.mkdir(parents=True, exist_ok=True)
    fixture = write_set(tmp_path / 'setups-captures', board_entries())
    with spawn_fixture_ceres(fixture) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
        page.emulate_media(reduced_motion='reduce')
        page.add_style_tag(content='*, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; caret-color: transparent !important; }')
        install_routes(page, fixture)
        page.goto(f'{base}/?player={SAMPLE}')
        page.wait_for_selector('#plot-tbody tr.plot-row')
        page.wait_for_load_state('networkidle')
        _menu(page)
        _capture(page, captures, '01-live-menu-open.png')
        assert page.locator('#hdr-menu').is_visible()
        assert page.locator('#hdr-menu [data-menu-entry="planning"]').count() == 1

        page.locator('#hdr-menu [data-menu-entry="planning"]').click()
        page.wait_for_function("document.body.dataset.boardMode === 'planning'")
        assert len(_saved(page)['plans']) == 1
        assert _selected(page)['name'] == 'Plan 1'
        _menu(page)
        _capture(page, captures, '02-planning-menu-one-setup.png')
        assert page.locator('#hdr-menu').get_by_text('Plan 1').count() >= 1

        _entry(page, 'setup-new')
        page.wait_for_function("document.body.dataset.boardPlanName === 'New setup'")
        plan_id = _saved(page)['selected_plan_id']
        page.once('dialog', lambda dialog: dialog.accept('Grain setup'))
        _entry(page, 'setup-rename')
        page.wait_for_function("document.body.dataset.boardPlanName === 'Grain setup'")
        assert _selected(page)['name'] == 'Grain setup'
        # The native prompt is browser chrome; capture the resulting renamed state in-app.
        _menu(page)
        _capture(page, captures, '04-rename.png')
        assert page.locator('#hdr-menu').get_by_text('Grain setup').count() >= 1

        _entry(page, 'setup-duplicate')
        page.wait_for_function("document.body.dataset.boardPlanName === 'Grain setup copy'")
        duplicate_id = _saved(page)['selected_plan_id']
        assert duplicate_id != plan_id and len(_saved(page)['plans']) == 3
        page.once('dialog', lambda dialog: dialog.accept('Start from live'))
        _entry(page, 'setup-rename')
        page.wait_for_function("document.body.dataset.boardPlanName === 'Start from live'")
        assert _selected(page)['name'] == 'Start from live'
        assert len(_saved(page)['plans']) == 3
        _menu(page)
        _capture(page, captures, '03-planning-menu-three-setups.png')
        assert page.locator('#hdr-menu [data-menu-entry^="setup:"]').count() == 3
        assert _saved(page)['selected_plan_id'] == duplicate_id

        def confirm_delete(dialog):
            _capture(page, captures, '05-delete-confirm.png')
            dialog.accept()
        page.once('dialog', confirm_delete)
        _entry(page, 'setup-delete')
        page.wait_for_function('(args) => !!JSON.parse(localStorage.getItem(args[0])).tombstones[args[1]]', arg=[PLAN_KEY, duplicate_id])
        assert duplicate_id not in _saved(page)['plans']
        toast = page.locator('#ceres-stage-toast')
        toast.wait_for(state='visible')
        _capture(page, captures, '06-undo-notice.png')
        assert toast.locator('.stage-toast-undo').is_visible()
        toast.locator('.stage-toast-undo').click()
        page.wait_for_function('(args) => !!JSON.parse(localStorage.getItem(args[0])).plans[args[1]]', arg=[PLAN_KEY, duplicate_id])
        assert len(_saved(page)['plans']) == 3
        assert _saved(page)['selected_plan_id'] == duplicate_id
        assert page.locator('#hdr-menu').is_hidden()
        assert page.evaluate("document.body.dataset.boardMode") == 'planning'
        assert _selected(page)['name'] == 'Start from live'
        assert page.locator('#hdr-place').inner_text().strip() == 'Planning · Start from live'
        _capture(page, captures, '07-planning-board-header.png')
        browser.close()
