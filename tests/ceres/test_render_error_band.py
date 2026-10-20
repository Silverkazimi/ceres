""" guard for fixed-height plot render-error stand-ins."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def test_render_error_stand_in_keeps_band_height_and_logs_plot(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 945})
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto(f"{base_url}/?player=sample01", timeout=15_000)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length >= 1", timeout=60_000)
    page.evaluate("() => { window.fetch = () => new Promise(() => {}); return 0; }")

    row = page.locator("#plot-tbody > tr.plot-row").first
    uid = row.get_attribute("data-deed-uid")
    assert uid
    row.click(timeout=10_000)
    band = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] .editor-band')
    band.wait_for(timeout=30_000)
    normal_height = band.evaluate("node => node.getBoundingClientRect().height")

    page.evaluate(
        """async uid => {
 const rows = await import('/static/rows.js');
 const table = await import('/static/table.js');
 const row = table.CURRENT_ROWS.find(candidate => candidate.deed_uid === uid);
 if (!row) throw new Error(`plot row ${uid} is unavailable`);
 const workers = Array.isArray(row.workers) ? row.workers : [];
 let filters = 0;
 const injectedWorkers = new Proxy(workers, {
 get(target, property, receiver) {
 if (property === 'filter' && ++filters === 2) {
 return () => { throw new Error(' injected render failure'); };
 }
 return Reflect.get(target, property, receiver);
 },
 });
 Object.defineProperty(row, 'workers', {
 configurable: true,
 get: () => injectedWorkers,
 });
 rows.updateMountedRow(uid);
 delete row.workers;
 row.workers = workers;
 }""",
        uid,
    )

    error_band = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] .editor-band[data-eos-render-error]')
    error_band.wait_for(timeout=10_000)
    stand_in_height = error_band.evaluate("node => node.getBoundingClientRect().height")
    retry = error_band.locator(".expand-retry-link")
    retry.wait_for(state="visible")
    page.close()

    assert abs(stand_in_height - normal_height) <= 1, {"normal": normal_height, "stand_in": stand_in_height}
    assert any(uid in message and "injected render failure" in message for message in console_errors), console_errors
