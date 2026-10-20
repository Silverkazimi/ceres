""" the width guard at the remaining common viewports.

Owner (): "1/3 of the page is empty space in the badge column."
Owner (): "DO NOT make Ceres exceptions just for me. We are building a
website that must work for everyone. However, scaling windows is a common thing".

test_scaling_guard.py covers the headline matrix (1526x730@1.25,
1280x720@1.5, 1366x768, 1440x900, 1536x864@1.25, 1920x1080, 2560x1440). This
file runs the SAME column and picker checks (imported from it, so the two can
never drift) at the other viewports people actually have, each with a painted
page scrollbar (the shared test browser drops --hide-scrollbars):
 1280x720 @1.0 (HD), @1.25 (1600x900 at 125%) -> card view (content <= 1279)
 1296x800 @1.0 -- the narrowest table (content 1281 px)
 1920x1080 @1.5 (2880x1620 at 150%) -> card view
 2560x1440 @1.5 (4K at 150%) -> table at 1706 CSS px
At every one: the width classes equal the formula, no page zoom, and in
the table view each column is judged against its CONTENT (no column holds more
than its fair share of the spare width, none above 25% unless its content needs
it, growth spread <= 1.6, no clipped cell or header, no side scrolling); the
worker picker shows as many cards per row as fit (desktop) or the mobile rule's
count (card view). A resize round trip returns every column to the same width.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_ready
from .test_width_guard import _APPROVED_FORMULA_JS
from .test_scaling_guard import _COLUMNS_JS, _PICKER_JS, _check_columns, _check_picker, _open_picker


VIEWPORTS = [
    (1280, 720, 1.0), (1280, 720, 1.25),
    (1296, 800, 1.0),
    (1920, 1080, 1.5),
    (2560, 1440, 1.5),
]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _load(live_browser, width: int, height: int, dpr: float) -> tuple[Page, object]:
    browser, base_url = live_browser
    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=dpr)
    context.add_init_script("try { localStorage.setItem('ceres:picker-view:v1:player0005', 'grid'); } catch (e) {}")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page._s9360_errors = errors
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0"
        " || document.querySelectorAll('#plot-card-list .plot-card').length > 0",
        timeout=60_000,
    )
    wait_ready(page)
    settle(page)
    return page, context


def _table_visible(page: Page) -> bool:
    return page.evaluate(
        "(() => { const s = document.getElementById('plot-table-scroll');"
        " return !!s && getComputedStyle(s).display !== 'none'; })()"
    )


@pytest.mark.parametrize(("width", "height", "dpr"), VIEWPORTS, ids=[f"{w}x{h}@{d}" for w, h, d in VIEWPORTS])
def test_common_viewport_has_no_unclaimed_slack(live_browser, width: int, height: int, dpr: float) -> None:
    page, context = _load(live_browser, width, height, dpr)
    try:
        tiers = page.evaluate(_APPROVED_FORMULA_JS)
        assert tiers["zoom"] == "1", json.dumps(tiers, sort_keys=True)
        assert tiers["actual"] == tiers["expected"], json.dumps(tiers, sort_keys=True)
        table = _table_visible(page)
        if not table:
            got = page.evaluate(
                "({cards: document.querySelectorAll('#plot-card-list .plot-card').length,"
                " docCW: document.documentElement.clientWidth, docSW: document.documentElement.scrollWidth})"
            )
            assert "ceres-lw-le-1279" in tiers["actual"], json.dumps(got)
            assert got["cards"] > 0, json.dumps(got)
            assert got["docSW"] <= got["docCW"] + 1, json.dumps(got)
        else:
            got = page.evaluate(_COLUMNS_JS)
            failures = _check_columns(got)
            assert not failures, "; ".join(failures) + "\n" + json.dumps(got, sort_keys=True)
        _open_picker(page, table)
        grid = page.evaluate(_PICKER_JS)
        failures = _check_picker(grid, phone=not table)
        assert not failures, "; ".join(failures) + "\n" + json.dumps(grid, sort_keys=True)
        assert not page._s9360_errors, page._s9360_errors
    finally:
        page.close()
        context.close()


def test_resize_round_trip_returns_every_column(live_browser) -> None:
    """ finding 2 in the model: the browser sizes the columns, so a
 resize away and back must return every column to the same width (no JS state
 left behind), and the intermediate width must itself pass the column checks."""
    page, context = _load(live_browser, 1440, 900, 1.0)
    try:
        before = page.evaluate(_COLUMNS_JS)
        page.set_viewport_size({"width": 1700, "height": 900})
        settle(page)
        wide = page.evaluate(_COLUMNS_JS)
        failures = _check_columns(wide)
        assert not failures, "; ".join(failures) + "\n" + json.dumps(wide, sort_keys=True)
        assert wide["container"] > before["container"] + 200, json.dumps([before["container"], wide["container"]])
        page.set_viewport_size({"width": 1440, "height": 900})
        settle(page)
        after = page.evaluate(_COLUMNS_JS)
        assert all(abs(a - b) <= 0.5 for a, b in zip(before["widths"], after["widths"])), json.dumps(
            {"before": before["widths"], "after": after["widths"]})
        assert not page._s9360_errors, page._s9360_errors
    finally:
        page.close()
        context.close()
