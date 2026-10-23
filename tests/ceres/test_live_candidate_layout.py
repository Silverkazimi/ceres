from __future__ import annotations


import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres
from . import board_basic_recording as recording


TARGET_DEED = recording.OVERCAP.uid
TABLE_EDGE_BUDGET_PX = 9  # the 8.5px desktop table edge budget, rounded up



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
          const row = document.querySelector('.candidate-row:not(.candidate-row-item)');
          const box = node => {
            const rect = node.getBoundingClientRect();
            return {left: rect.left, right: rect.right};
          };
          const within = (inner, outer) => inner.left >= outer.left - 1 && inner.right <= outer.right + 1;
          const close = document.querySelector('.inline-picker .picker-close');
          const metricValues = [...document.querySelectorAll('.candidate-row:first-child .candidate-metric strong')];
          const sr = scroll.getBoundingClientRect();
          const pr = panel.getBoundingClientRect();
          const dr = plot.getBoundingClientRect();
          return {
            scrollLeft: scroll.scrollLeft,
            tableScrollWidth: scroll.scrollWidth,
            panelLeft: pr.left,
            panelRight: pr.right,
            closeRight: close.getBoundingClientRect().right,
            tableOverhang: scroll.scrollWidth - scroll.clientWidth,
            scrollLeftEdge: sr.left,
            scrollRightEdge: sr.right,
            plotRight: dr.right,
            panelNestedOverflow: panel.scrollWidth > panel.clientWidth + 1,
            listNestedOverflow: list.scrollWidth > list.clientWidth + 1,
            pickerNestedOverflow: picker.scrollWidth > picker.clientWidth + 1,
            rowMetricCaptions: [...row.querySelectorAll('.candidate-metrics > .candidate-metric')].map(metric => {
              const caption = metric.querySelector(':scope > small');
              return {text: caption ? caption.textContent.trim() : '', inside: !!caption && within(box(caption), box(metric))};
            }),
            metricValueClipped: metricValues.some(node => node.scrollWidth > node.clientWidth + 1),
          };
        }"""
    )


def test_candidate_panel_stays_in_visible_slice_and_reflows(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    # The table (and its inline picker) is the desktop tier: a layout width of 1279 rem-px or
    # less (`ceres-lw-le-1279`, measured inside the stable scrollbar gutter) renders the board
    # as mobile cards instead, so the narrowest desktop case is a 1300 px window.
    page = browser.new_page(viewport={"width": 1300, "height": 900})
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
    # text_content: the metric captions are upper-cased by CSS, the words are what is checked
    picker_text = expanded.locator(".inline-picker").text_content() or ""
    assert "Boostable" in picker_text
    assert "Base PP" in picker_text
    assert "Terrain" in picker_text
    assert "Total PP change" in picker_text
    assert "Best for this plot" not in picker_text
    assert "Ability cards" not in picker_text
    assert "Show everything" not in picker_text
    assert "ED 3" not in picker_text
    assert expanded.locator(".candidate-row").first.locator(".candidate-identity img").count() == 3
    # Worker cards caption every metric themselves (there is no worker column header row to
    # align against any more): the four PP metrics each carry their own caption inside their box.
    assert expanded.locator(".candidate-columns").count() == 0
    geometry = _geometry(page)
    captions = geometry["rowMetricCaptions"]
    assert [c["text"] for c in captions[:4]] == ["Base PP", "Boostable", "Terrain", "Total PP change"]
    assert all(c["text"] and c["inside"] for c in captions), captions
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
        settle(page)
        geometry = _geometry(page)
        assert geometry["panelLeft"] >= geometry["scrollLeftEdge"] - 1
        # The desktop table is deliberately 8.5px wider than its scroller (ceres.css
        # `#plot-table-scroll > table.plot-table { inline-size: calc(100% + 8.5px) }`), and the
        # expand-row bands span the table, so the panel box may run under that budget -- its
        # controls may not: the Close button must sit inside the visible slice.
        assert geometry["panelRight"] <= geometry["scrollRightEdge"] + TABLE_EDGE_BUDGET_PX
        assert geometry["closeRight"] <= geometry["scrollRightEdge"] + 1
        assert geometry["plotRight"] <= geometry["scrollRightEdge"] + 1
        assert not geometry["panelNestedOverflow"]
        assert not geometry["listNestedOverflow"]

    # Text-size reflow inside the desktop tier (larger root sizes shrink the rem layout width;
    # every pair stays above the 1279 card-board threshold, whose picker is the mobile sheet).
    for width, root_size in ((1300, 16), (1536, 18), (1920, 22), (1300, 16)):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("size => { document.documentElement.style.fontSize = `${size}px`; }", root_size)
        settle(page)
        geometry = _geometry(page)
        assert geometry["panelLeft"] >= geometry["scrollLeftEdge"] - 1
        # The desktop table is deliberately 8.5px wider than its scroller (ceres.css
        # `#plot-table-scroll > table.plot-table { inline-size: calc(100% + 8.5px) }`), and the
        # expand-row bands span the table, so the panel box may run under that budget -- its
        # controls may not: the Close button must sit inside the visible slice.
        assert geometry["panelRight"] <= geometry["scrollRightEdge"] + TABLE_EDGE_BUDGET_PX
        assert geometry["closeRight"] <= geometry["scrollRightEdge"] + 1
        assert geometry["plotRight"] <= geometry["scrollRightEdge"] + 1
        assert not geometry["panelNestedOverflow"]
        assert not geometry["listNestedOverflow"]
        assert not geometry["metricValueClipped"]

    page.close()
