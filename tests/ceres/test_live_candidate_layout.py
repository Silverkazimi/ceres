from __future__ import annotations


import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


TARGET_DEED = "I-294-99ed4b7a4d170e"



@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _geometry(page) -> dict[str, float | bool]:
    return page.evaluate(
        """() => {
 const scroll = document.querySelector('#plot-table-scroll');
 const panel = document.querySelector('.candidate-visible-slice');
 const plot = document.querySelector('.candidate-plot-change');
 const list = document.querySelector('.candidate-list');
 const picker = document.querySelector('.inline-picker');
 const header = [...document.querySelectorAll('.candidate-columns:not(.is-item-columns) > span')];
 const row = document.querySelector('.candidate-row:not(.candidate-row-item)');
 const box = node => {
 const rect = node.getBoundingClientRect();
 return {left: rect.left, right: rect.right};
 };
 const metricValues = [...document.querySelectorAll('.candidate-row:first-child .candidate-metric strong')];
 const sr = scroll.getBoundingClientRect();
 const pr = panel.getBoundingClientRect();
 const dr = plot.getBoundingClientRect();
 return {
 scrollLeft: scroll.scrollLeft,
 tableScrollWidth: scroll.scrollWidth,
 panelLeft: pr.left,
 panelRight: pr.right,
 scrollLeftEdge: sr.left,
 scrollRightEdge: sr.right,
 plotRight: dr.right,
 panelNestedOverflow: panel.scrollWidth > panel.clientWidth + 1,
 listNestedOverflow: list.scrollWidth > list.clientWidth + 1,
 pickerNestedOverflow: picker.scrollWidth > picker.clientWidth + 1,
 headerMetricBoxes: header.slice(2, 6).map(box),
 rowMetricBoxes: [...row.querySelectorAll('.candidate-metric')].map(box),
 metricValueClipped: metricValues.some(node => node.scrollWidth > node.clientWidth + 1),
 };
 }"""
    )


def test_candidate_panel_stays_in_visible_slice_and_reflows(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(base_url)
    row = page.locator(f"tr.plot-row[data-deed-uid='{TARGET_DEED}']")
    row.wait_for(timeout=30_000)
    initial = page.evaluate(
        """() => {
 const scroll = document.querySelector('#plot-table-scroll');
 const table = document.querySelector('#plot-table');
 return {scrollWidth: scroll.scrollWidth, tableWidth: table.getBoundingClientRect().width};
 }"""
    )
    row.click()
    expanded = page.locator(f"tr.expand-row[data-deed-uid='{TARGET_DEED}']")
    expanded.locator(".slot-editor").wait_for(timeout=30_000)
    worker = expanded.locator(".slot-group[data-group-id='worker']")
    target = worker.locator(".slot-tile[data-actionable='1']").first
    with page.expect_request("**/api/plots/*/candidates*") as request:
        target.click()
    assert "sort=construction_pp" in request.value.url
    expanded.locator(".candidate-row").first.wait_for(timeout=30_000)
    picker_text = expanded.locator(".inline-picker").inner_text()
    assert "Boostable" in picker_text
    assert "Base PP" in picker_text
    assert "Terrain" in picker_text
    assert "Plot change" in picker_text
    assert "Best for this plot" not in picker_text
    assert "Ability cards" not in picker_text
    assert "Show everything" not in picker_text
    assert "ED 3" not in picker_text
    assert expanded.locator(".candidate-row").first.locator(".candidate-identity img").count() == 3
    geometry = _geometry(page)
    assert len(geometry["headerMetricBoxes"]) == 4
    assert len(geometry["rowMetricBoxes"]) == 4
    for header_box, row_box in zip(geometry["headerMetricBoxes"], geometry["rowMetricBoxes"]):
        assert row_box["left"] == pytest.approx(header_box["left"], abs=1)
        assert row_box["right"] == pytest.approx(header_box["right"], abs=1)
    assert not geometry["pickerNestedOverflow"]
    opened = page.evaluate(
        """() => {
 const scroll = document.querySelector('#plot-table-scroll');
 const table = document.querySelector('#plot-table');
 return {scrollWidth: scroll.scrollWidth, tableWidth: table.getBoundingClientRect().width};
 }"""
    )
    assert opened["scrollWidth"] == pytest.approx(initial["scrollWidth"], abs=1)
    assert opened["tableWidth"] == pytest.approx(initial["tableWidth"], abs=1)

    scroll = page.locator("#plot-table-scroll")
    maximum = scroll.evaluate("node => node.scrollWidth - node.clientWidth")
    for left in (0, maximum / 2, maximum):
        scroll.evaluate("(node, value) => { node.scrollLeft = value; }", left)
        page.wait_for_timeout(50)
        geometry = _geometry(page)
        assert geometry["panelLeft"] >= geometry["scrollLeftEdge"] - 1
        assert geometry["panelRight"] <= geometry["scrollRightEdge"] + 1
        assert geometry["plotRight"] <= geometry["scrollRightEdge"] + 1
        assert not geometry["panelNestedOverflow"]
        assert not geometry["listNestedOverflow"]

    for width, root_size in ((1280, 16), (800, 20), (800, 24), (375, 32)):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("size => { document.documentElement.style.fontSize = `${size}px`; }", root_size)
        page.wait_for_timeout(100)
        geometry = _geometry(page)
        assert geometry["panelLeft"] >= geometry["scrollLeftEdge"] - 1
        assert geometry["panelRight"] <= geometry["scrollRightEdge"] + 1
        assert geometry["plotRight"] <= geometry["scrollRightEdge"] + 1
        assert not geometry["panelNestedOverflow"]
        assert not geometry["listNestedOverflow"]
        assert not geometry["metricValueClipped"]

    page.close()
