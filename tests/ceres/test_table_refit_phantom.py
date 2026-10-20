"""Live guards for 's cached table-width phantom, in the  model.

 was a JS-cached column width that left a phantom horizontal overflow
after a scrollbar appeared, a window shrink or a filter round trip. 
removed the cache: the browser sizes the table to its board width. These
guards drive the same sequences and require, at every step: no overflow of the
clipping scroller (nothing hidden, no side scroll), the table exactly spanning
its scroller, no clipped cell, and -- through a filter round trip -- every
column back at the same width. No page errors.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_ready


FULL_PLOT_COUNT = 129


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _wait_full_list(page: Page) -> None:
    page.wait_for_function(
        f"document.querySelectorAll('#plot-tbody > tr.plot-row').length === {FULL_PLOT_COUNT}",
        timeout=30_000,
    )
    wait_ready(page)
    settle(page)


def _metrics(page: Page) -> dict:
    return page.evaluate(
        """() => {
            const scroller = document.querySelector('#plot-table-scroll');
            const table = document.querySelector('#plot-table');
            const first = document.querySelector('#plot-tbody > tr.plot-row');
            return {
                rows: document.querySelectorAll('#plot-tbody > tr.plot-row').length,
                verticalScrollbar: window.innerWidth - document.documentElement.clientWidth,
                scrollerClientWidth: scroller.clientWidth,
                scrollWidth: scroller.scrollWidth,
                over: scroller.scrollWidth - scroller.clientWidth,
                tableWidth: table.getBoundingClientRect().width,
                pageOver: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                clipped: [...document.querySelectorAll('#plot-tbody tr.plot-row td')]
                  .filter(td => td.scrollWidth > td.clientWidth + 2).length,
                columns: first ? [...first.children].map(td => Math.round(td.getBoundingClientRect().width * 10) / 10) : [],
            };
        }"""
    )


def _assert_fits(measured: dict) -> None:
    assert measured["over"] <= 1, measured
    assert measured["pageOver"] <= 0, measured
    assert abs(measured["tableWidth"] - measured["scrollerClientWidth"]) <= 1, measured
    assert measured["clipped"] == 0, measured


def _page(live_browser: tuple[Browser, str], width: int, height: int) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": width, "height": height})
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page._ir1858_page_errors = page_errors
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    _wait_full_list(page)
    return page


def _classic_scrollbar(page: Page) -> int:
    width = page.evaluate("() => window.innerWidth - document.documentElement.clientWidth")
    assert width > 0, "classic scrollbar precondition failed; the launch flag is missing"
    return width


def _filter_value(page: Page) -> str:
    value = page.evaluate(
        """async () => {
            const payload = await (await fetch('/api/board')).json();
            const row = (payload.plots || []).find((plot) => plot.plot);
            return row?.plot || '';
        }"""
    )
    assert value, "the live board supplied no real plot value for the filter round-trip"
    return value


def _apply_filter(page: Page, value: str) -> int:
    page.locator('#text-filter').fill(value)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0 && document.querySelectorAll('#plot-tbody > tr.plot-row').length < 20",
        timeout=10_000,
    )
    count = page.locator('#plot-tbody > tr.plot-row').count()
    assert 0 < count < 20
    return count


def _clear_filter(page: Page) -> int:
    page.locator('#text-filter').fill('')
    _wait_full_list(page)
    count = page.locator('#plot-tbody > tr.plot-row').count()
    assert count == FULL_PLOT_COUNT
    return count


def _assert_no_page_errors(page: Page) -> None:
    assert not page._ir1858_page_errors, page._ir1858_page_errors


def test_scrollbar_appearing_after_load_leaves_no_phantom(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser, 1920, 5000)
    before = _metrics(page)
    assert before["rows"] == FULL_PLOT_COUNT
    assert before["verticalScrollbar"] == 0, before
    _assert_fits(before)
    page.set_viewport_size({"width": 1920, "height": 1080})
    settle(page)
    after = _metrics(page)
    assert after["verticalScrollbar"] > 0, after
    _assert_fits(after)
    _assert_no_page_errors(page)
    page.close()


def test_window_shrink_and_restore_always_fit(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser, 1920, 1080)
    _classic_scrollbar(page)
    full = _metrics(page)
    _assert_fits(full)
    for width in (1800, 1536, 1366):
        page.set_viewport_size({"width": width, "height": 1080})
        settle(page)
        narrowed = _metrics(page)
        _assert_fits(narrowed)
        assert narrowed["scrollerClientWidth"] < full["scrollerClientWidth"], narrowed
    page.set_viewport_size({"width": 1920, "height": 1080})
    settle(page)
    restored = _metrics(page)
    _assert_fits(restored)
    assert restored["columns"] == pytest.approx(full["columns"], abs=0.5), (full, restored)
    _assert_no_page_errors(page)
    page.close()


@pytest.mark.parametrize("width", [1536, 1920])
def test_filter_round_trip_restores_every_column(live_browser: tuple[Browser, str], width: int) -> None:
    page = _page(live_browser, width, 1080)
    _classic_scrollbar(page)
    first = _metrics(page)
    _assert_fits(first)
    value = _filter_value(page)
    _apply_filter(page, value)
    _assert_fits(_metrics(page))
    _clear_filter(page)
    settle(page)
    after = _metrics(page)
    _assert_fits(after)
    assert after["columns"] == pytest.approx(first["columns"], abs=0.5), (first, after)
    _assert_no_page_errors(page)
    page.close()
