"""Synthetic desktop submenu captures, retained by core browser CI only."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

playwright = pytest.importorskip('playwright.sync_api')
pytestmark = pytest.mark.browser
PLAN_KEY = f'ceres:board-plans:v2:{SAMPLE}'


def _saved(page):
    return page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', PLAN_KEY)


def _menu(page, variant):
    if page.locator('#hdr-menu').is_hidden():
        page.locator('#hdr-menu-btn').click()
    page.wait_for_selector('#hdr-menu:not([hidden])')
    if variant == 'B':
        page.locator('#hdr-menu [data-menu-entry="planning"]').click()
        assert page.locator('[data-menu-submenu]').is_visible()
        assert page.locator('[data-menu-submenu] [data-menu-entry^="setup:"]').count() >= 1


def _capture(page, folder, variant, name):
    assert page.locator('#hdr-menu').is_visible() or name == '05-long-title-notice.png'
    page.screenshot(path=str(folder / f'{variant}-{name}'), full_page=False, animations='disabled')


def _assert_notice_clear(page):
    notice = page.locator('#ceres-stage-toast').bounding_box()
    assert notice
    for selector in ('#hdr-place', '.controls-row', '#hdr-menu:not([hidden])',
                     '#hdr-menu [data-menu-submenu]:not([hidden])'):
        for target in page.locator(selector).all():
            box = target.bounding_box()
            if box:
                assert (notice['x'] + notice['width'] <= box['x']
                        or box['x'] + box['width'] <= notice['x']
                        or notice['y'] + notice['height'] <= box['y']
                        or box['y'] + box['height'] <= notice['y']), selector


def test_planning_setup_ci_captures(tmp_path):
    output = os.environ.get('CERES_CI_CAPTURES')
    if not output:
        pytest.skip('CERES_CI_CAPTURES is unset')
    captures = Path(output)
    captures.mkdir(parents=True, exist_ok=True)
    fixture = write_set(tmp_path / 'setups-captures', board_entries())
    with spawn_fixture_ceres(fixture) as base, playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        for variant in ('A', 'B'):
            page = browser.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
            page.on('dialog', lambda dialog: pytest.fail(f'unexpected native dialog: {dialog.type}'))
            page.emulate_media(reduced_motion='reduce')
            install_routes(page, fixture)
            page.goto(f'{base}/?player={SAMPLE}')
            page.wait_for_selector('#plot-tbody tr.plot-row')
            page.wait_for_load_state('networkidle')
            page.evaluate('(value) => document.body.dataset.hdrMenuVariant = value', variant)
            _menu(page, variant) if variant == 'B' else page.locator('#hdr-menu-btn').click()
            assert page.locator('#hdr-menu [data-menu-entry="planning"]').count() == 1
            _capture(page, captures, variant, '01-live-menu.png')
            if variant == 'B':
                # Select the first setup from the nested panel to enter Planning.
                page.locator('[data-menu-submenu] [data-menu-entry^="setup:"]').first.click()
            else:
                page.locator('#hdr-menu [data-menu-entry="planning"]').click()
            page.wait_for_function("document.body.dataset.boardMode === 'planning'")
            for expected_count in (2, 3):
                _menu(page, variant)
                page.locator('#hdr-menu [data-menu-entry="setup-new"]').click()
                page.wait_for_function("document.body.dataset.boardPlanName === 'New setup'")
                page.wait_for_function('(args) => Object.keys(JSON.parse(localStorage.getItem(args[0])).plans).length === args[1]', arg=[PLAN_KEY, expected_count])
            assert len(_saved(page)['plans']) == 3
            _menu(page, variant)
            assert page.locator('#hdr-menu [data-menu-entry^="setup:"]').count() == 3
            assert page.locator('#hdr-menu .hdr-menu-setups').count() == (1 if variant == 'A' else 0)
            _capture(page, captures, variant, '02-planning-three-setups.png')

            current = _saved(page)['selected_plan_id']
            page.locator(f'#hdr-menu [data-setup-rename="{current}"]').click()
            field = page.locator('#hdr-menu .hdr-menu-rename')
            assert field.is_visible() and field.input_value() == 'New setup'
            field.fill('Grain setup')
            _capture(page, captures, variant, '03-inline-rename.png')
            field.press('Enter')
            page.wait_for_function("document.body.dataset.boardPlanName === 'Grain setup'")
            assert _saved(page)['plans'][current]['name'] == 'Grain setup'

            _menu(page, variant)
            page.locator(f'#hdr-menu [data-setup-delete="{current}"]').click()
            page.wait_for_function('(args) => !!JSON.parse(localStorage.getItem(args[0])).tombstones[args[1]]', arg=[PLAN_KEY, current])
            assert current not in _saved(page)['plans']
            toast = page.locator('#ceres-stage-toast')
            assert toast.locator('.stage-toast-undo').is_visible()
            _menu(page, variant)
            _assert_notice_clear(page)
            _capture(page, captures, variant, '04-after-delete-undo.png')
            toast.locator('.stage-toast-undo').click()
            page.wait_for_function('(args) => !!JSON.parse(localStorage.getItem(args[0])).plans[args[1]]', arg=[PLAN_KEY, current])
            assert len(_saved(page)['plans']) == 3

            # A long title checks actual rectangles instead of a CSS token alone.
            _menu(page, variant)
            page.locator(f'#hdr-menu [data-setup-rename="{current}"]').click()
            field = page.locator('#hdr-menu .hdr-menu-rename')
            field.fill('Grain setup for the entire northern production region')
            field.press('Enter')
            page.wait_for_function("document.body.dataset.boardPlanName.includes('northern production')")
            toast.wait_for(state='visible')
            assert page.locator('#hdr-place').inner_text().startswith('Planning · Grain setup')
            _menu(page, variant)
            _assert_notice_clear(page)
            _capture(page, captures, variant, '05-long-title-notice.png')
            page.close()
        browser.close()
