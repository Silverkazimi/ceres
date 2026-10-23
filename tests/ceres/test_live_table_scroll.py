"""Live browser regression: the plot table never scrolls sideways ().

History: the maintainer reported two horizontal scrollbars under the table
(2026-08-28: "there are two horizontal scroll bars under the main table of
plots, both do the same and trigger the other scroll bar") -- `.table-scroll`'s
own scrollbar plus the separate sticky-bottom proxy built for . 
removed the reason for both: the table is sized by CSS to its board width, so
there is NO horizontal scroll at any width -- no proxy, and `.table-scroll`
clips on x instead of scrolling. This guard counts what a user would see as a
horizontal scrollbar, from computed state, at a card-view width, the narrowest
common table widths and Full HD.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres, wait_ready
from .table_edge import OVERHANG_JS, TABLE_OVERHANG_PX, overhang_problems


@pytest.fixture(scope="module")
def browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (instance, base_url):
        yield instance, base_url


# A horizontal scroll box (`overflow-x: auto`/`scroll`) whose content genuinely
# overflows, on the table's ancestor chain, plus the page itself.
_HSCROLL_JS = """
() => {
  const table = document.getElementById('plot-table');
  if (!table) return null;
  const candidates = [];
  for (let el = table; el; el = el.parentElement) candidates.push(el);
  let boxes = 0;
  for (const node of candidates) {
    const cs = getComputedStyle(node);
    if ((cs.overflowX === 'auto' || cs.overflowX === 'scroll') && node.scrollWidth > node.clientWidth + 1) boxes++;
  }
  const scroller = document.getElementById('plot-table-scroll');
  const doc = document.documentElement;
  return {
    gutter: window.innerWidth - document.body.getBoundingClientRect().width,
    boxes,
    proxy: !!document.getElementById('table-hscroll-proxy'),
    scrollerOverflowX: getComputedStyle(scroller).overflowX,
    // the deliberate 8px edge budget (table_edge.py) is the only overhang.
    tableHidden: scroller.scrollWidth > scroller.clientWidth + """ + str(TABLE_OVERHANG_PX) + """ + 1,
    overhang: (""" + OVERHANG_JS + """)(),
    pageSW: doc.scrollWidth, pageCW: doc.clientWidth,
    tableVisible: getComputedStyle(scroller).display !== 'none',
  };
}
"""


@pytest.mark.parametrize("width", [900, 1296, 1366, 1440, 1920])
def test_no_horizontal_scrollbar_at_any_width(browser: tuple[Browser, str], width: int) -> None:
    instance, base_url = browser
    page = instance.new_page(viewport={"width": width, "height": 800})
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0"
        " || document.querySelectorAll('#plot-card-list .plot-card').length > 0",
        timeout=60_000,
    )
    wait_ready(page)
    settle(page)
    got = page.evaluate(_HSCROLL_JS)
    page.close()
    assert got is not None
    # card view at content <= 1279; content = window - the page's own scrollbar
    # gutter (thin: 10px in Chromium, so 1289 is the last card-view window).
    assert got["tableVisible"] == (width - got["gutter"] > 1279), got
    assert got["boxes"] == 0, f"horizontal scroll box at {width}px: {got}"
    assert got["proxy"] is False, got
    assert got["scrollerOverflowX"] == "clip", got
    assert got["pageSW"] <= got["pageCW"], got
    if got["tableVisible"]:
        assert got["tableHidden"] is False, f"table wider than its clipping scroller at {width}px: {got}"
        assert not overhang_problems(got["overhang"]), f"{overhang_problems(got['overhang'])} at {width}px: {got}"


def test_desktop_table_header_reaches_both_document_edges(browser: tuple[Browser, str]) -> None:
    """: the sticky table header must not stop inside either board gutter."""
    instance, base_url = browser
    page = instance.new_page(viewport={"width": 1440, "height": 800})
    try:
        page.goto(f"{base_url}/?player=sample01")
        page.wait_for_function(
            "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
            timeout=60_000,
        )
        wait_ready(page)
        settle(page)
        got = page.evaluate(
            """() => {
              const viewport = document.documentElement.getBoundingClientRect();
              const scroller = document.getElementById('plot-table-scroll').getBoundingClientRect();
              const head = document.getElementById('table-head-sticky').getBoundingClientRect();
              return {
                viewportLeft: viewport.left, viewportRight: viewport.right,
                scrollerLeft: scroller.left, scrollerRight: scroller.right,
                headLeft: head.left, headRight: head.right,
              };
            }"""
        )
    finally:
        page.close()
    assert abs(got["scrollerLeft"] - got["viewportLeft"]) <= 1, got
    assert abs(got["scrollerRight"] - got["viewportRight"]) <= 1, got
    assert got["headLeft"] <= got["viewportLeft"] + 1, got
    assert got["headRight"] >= got["viewportRight"] - 1, got
