""" /  width guards: one authoritative width, no page zoom, no
dead space, no clipped cells, no tier flapping.

Matrix (, kept): 1280 / 1290 / 1440 CSS px x DPR 1.0 / 1.25 / 1.35 / 1.5,
with a clean AND a stale localStorage from the retired zoom scaler
(`ceres:contentW`, `ceres:autoZoom`).  removed the page zoom: the width
classes are the plain board width in em (innerWidth - gutter, x 16 / root font),
the table is sized by CSS (width 100% of its scroller, no JS column widths), and
the stale keys must be ignored AND cleared. 1280 and 1290 are the card view
(<= 1279), 1440 the table at every DPR (no ">Full HD" special case any more).
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .conftest import settle, spawn_live_ceres, wait_ready


WIDTHS = (1280, 1290, 1440)
DPRS = (1.0, 1.25, 1.35, 1.5)
STALE = (False, True)


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _open(live_browser, width: int, dpr: float, stale: bool = False, *, browser: Browser | None = None,
          height: int = 800) -> tuple[Page, object]:
    shared, base_url = live_browser
    context = (browser or shared).new_context(viewport={"width": width, "height": height}, device_scale_factor=dpr)
    if stale:
        context.add_init_script(
            "try { if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1');"
            " localStorage.setItem('ceres:contentW', '1586'); localStorage.setItem('ceres:autoZoom', 'off'); } } catch (e) {}"
        )
    page = context.new_page()
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0"
        " || document.querySelectorAll('#plot-card-list .plot-card').length > 0",
        timeout=60_000,
    )
    wait_ready(page)
    settle(page)
    return page, context


# The board's content box measured from layout (rect), independently of
# scale.js availableTableWidth() and of the scroller.
_MAIN_CONTENT_JS = """
  const main = document.getElementById('board-main');
  const ms = getComputedStyle(main);
  const mainContent = main.getBoundingClientRect().width
    - parseFloat(ms.paddingLeft) - parseFloat(ms.paddingRight)
    - parseFloat(ms.borderLeftWidth) - parseFloat(ms.borderRightWidth);
"""


def _state(page: Page) -> dict:
    return page.evaluate(
        """async () => {
          """ + _MAIN_CONTENT_JS + """
          const tiers = [...document.documentElement.classList].filter(c => c.startsWith('ceres-lw-')).sort();
          const scroller = document.getElementById('plot-table-scroll');
          const table = document.getElementById('plot-table');
          const tableVisible = scroller && getComputedStyle(scroller).display !== 'none';
          const cardList = document.getElementById('plot-card-list');
          const cards = cardList && getComputedStyle(cardList).display !== 'none'
            ? cardList.querySelectorAll('.plot-card').length : 0;
          const cols = table ? [...table.querySelectorAll('colgroup col')] : [];
          const inlineColWidths = cols.filter(c => c.style.width).length;
          const rows = [...document.querySelectorAll('#plot-tbody tr.plot-row')];
          const clippedCells = rows.flatMap(tr => [...tr.children])
            .filter(td => td.scrollWidth > td.clientWidth + 2).length;
          const clippedHead = [...document.querySelectorAll('#plot-table thead th')]
            .filter(th => th.scrollWidth > th.clientWidth + 2).length;
          const scale = await import('/static/scale.js');
          const authoritative = scale.availableTableWidth();
          // The non-table consumers' path (scroller hidden) must see the same width.
          let hiddenFallback = -1;
          if (tableVisible) {
            const prior = scroller.style.display;
            scroller.style.display = 'none';
            hiddenFallback = scale.availableTableWidth();
            scroller.style.display = prior;
          }
          return {
            tiers, tableVisible, cards,
            rows: rows.length,
            scrollerCW: scroller ? scroller.clientWidth : 0,
            scrollerSW: scroller ? scroller.scrollWidth : 0,
            tableW: table ? table.offsetWidth : 0,
            inlineColWidths, clippedCells, clippedHead,
            authoritative, hiddenFallback, mainContent,
            zoom: getComputedStyle(document.documentElement).zoom,
            staleKeys: [localStorage.getItem('ceres:contentW'), localStorage.getItem('ceres:autoZoom')],
            ds: document.documentElement.dataset.ceresScale || '',
          };
        }"""
    )


@pytest.mark.parametrize("stale", STALE, ids=["cleanW", "staleW"])
@pytest.mark.parametrize("dpr", DPRS)
@pytest.mark.parametrize("width", WIDTHS)
def test_width_invariants(live_browser, width: int, dpr: float, stale: bool) -> None:
    page, context = _open(live_browser, width, dpr, stale)
    try:
        state = _state(page)
        detail = json.dumps(state, sort_keys=True)
        # Something must be VISIBLE -- the pre-fix 1280 dead state rendered a
        # table and then hid it without rendering cards.
        assert (state["tableVisible"] and state["rows"] > 0) or state["cards"] > 0, detail
        # no page zoom, and the retired scaler's saved keys are cleared.
        assert state["zoom"] == "1", detail
        assert state["staleKeys"] == [None, None], detail
        if state["tableVisible"]:
            # (a) the table spans its scroller exactly (no unclaimed board space)
            # nothing overflows the clipping scroller (nothing hidden), and no JS
            # column width is written any more (the browser sizes the columns).
            assert abs(state["tableW"] - state["scrollerCW"]) <= 1, detail
            assert state["scrollerSW"] <= state["scrollerCW"] + 1, detail
            assert state["inlineColWidths"] == 0, detail
            # (b) nothing is ellipsis-clipped for the real on-disk account data.
            assert state["clippedCells"] == 0, detail
            assert state["clippedHead"] == 0, detail
            # (c) the table's container spans the board's whole content box, and
            # the width every non-table consumer reads (scroller hidden) is that
            # same width.
            assert abs(state["scrollerCW"] - state["mainContent"]) <= 1, detail
            assert abs(state["hiddenFallback"] - state["scrollerCW"]) <= 1, detail
        else:
            # Mobile-card tier: the geometry source is the board's real content
            # box -- never 0, never a clamp, never the viewport API ().
            assert abs(state["authoritative"] - state["mainContent"]) <= 1, detail
    finally:
        page.close()
        context.close()


@pytest.mark.parametrize("stale", STALE, ids=["cleanW", "staleW"])
@pytest.mark.parametrize("dpr", (1.0, 1.35))
@pytest.mark.parametrize("width", WIDTHS)
def test_tier_classes_are_stable_across_content_height(live_browser, width: int, dpr: float, stale: bool) -> None:
    """(d) The tier must not depend on WHEN the measurement ran or on whether a
    page scrollbar currently exists (129 rows vs a filtered handful)."""
    page, context = _open(live_browser, width, dpr, stale)
    try:
        def tiers() -> list[str]:
            return page.evaluate(
                "[...document.documentElement.classList].filter(c => c.startsWith('ceres-lw-')).sort()"
            )

        loaded = tiers()
        page.evaluate("window.ceresScale.apply()")
        assert tiers() == loaded, f"tier changed on a re-measure with full content: {loaded} -> {tiers()}"
        # Shrink the content (page scrollbar may vanish) and re-measure. The
        # filter box MUST be usable -- a hidden box would skip the whole check.
        filter_input = page.locator("#text-filter")
        if "ceres-lw-le-1279" in loaded:
            # Card tier: the search box lives in the mobile search sheet.
            page.locator("#mobile-search-btn").click()
            filter_input.wait_for(state="visible")
        assert filter_input.count() == 1 and filter_input.is_visible(), "board text filter is not visible"
        value = page.evaluate(
            """async () => {
 const payload = await (await fetch('/api/board?player=sample01')).json();
 const row = (payload.plots || []).find(p => p.plot);
 return row ? String(row.plot) : '';
 }"""
        )
        before = page.evaluate("document.documentElement.scrollHeight")
        shown = page.evaluate(_SHOWN_PLOTS_JS)
        filter_input.fill(value)
        page.wait_for_function(f"({_SHOWN_PLOTS_JS})() < {shown}", timeout=10_000)
        settle(page)
        after = page.evaluate("document.documentElement.scrollHeight")
        assert after < before, f"the filter did not shrink the page ({before} -> {after})"
        page.evaluate("window.ceresScale.apply()")
        assert tiers() == loaded, f"tier changed with content height: {loaded} -> {tiers()}"
        filter_input.fill("")
        page.wait_for_function(f"({_SHOWN_PLOTS_JS})() === {shown}", timeout=10_000)
        settle(page)
        page.evaluate("window.ceresScale.apply()")
        assert tiers() == loaded, f"tier did not return: {loaded} -> {tiers()}"
    finally:
        page.close()
        context.close()


# the width classes and --ceres-vw resolve from the plain board width
# with the gutter read from <body>'s laid-out width: w = innerWidth - s; the
# class boundaries in em (w x 16 / root font), --ceres-vw = w / 100. No factor
# no ">Full HD" exception.
_APPROVED_FORMULA_JS = """() => {
  const root = document.documentElement;
  const s = window.innerWidth - document.body.getBoundingClientRect().width;
  const lw = window.innerWidth - s;
  const e = lw * 16 / (parseFloat(getComputedStyle(root).fontSize) || 16);
  const expected = [];
  const want = (name, on) => { if (on) expected.push(name); };
  want('ceres-lw-le-1919', e <= 1919);
  want('ceres-lw-le-1659', e <= 1659);
  want('ceres-lw-le-1439', e <= 1439);
  want('ceres-lw-le-1279', e <= 1279);
  want('ceres-lw-le-767', e <= 767);
  want('ceres-lw-ge-1400', e >= 1400);
  want('ceres-lw-le-1099', e <= 1099);
  want('ceres-lw-le-544', e <= 544);
  want('ceres-lw-le-960', e <= 960);
  return {
    gutter: s, expected: expected.sort(), expectedVw: lw / 100,
    actual: [...root.classList].filter(c => c.startsWith('ceres-lw-')).sort(),
    actualVw: parseFloat(root.style.getPropertyValue('--ceres-vw')),
    zoom: getComputedStyle(root).zoom,
    ds: root.dataset.ceresScale || '',
  };
}"""

_TIER_POINTS = [(1280, 1.0), (1294, 1.0), (1295, 1.0), (1439, 1.0), (1440, 1.0), (1919, 1.0), (1920, 1.0),
                (round(1920 / 1.35), 1.35)]


@pytest.fixture(scope="module")
def hidden_scrollbar_browser(live_browser):
    """The shared test browser paints classic scrollbars (conftest drops
    --hide-scrollbars); this second Chromium keeps headless's default HIDDEN
    scrollbars -- a reserved-but-unpainted gutter, where
    documentElement.clientWidth and the laid-out width disagree by 15px."""
    shared, _ = live_browser
    browser = shared.browser_type.launch()
    try:
        yield browser
    finally:
        browser.close()


@pytest.mark.parametrize("scrollbars", ("hidden", "painted"))
@pytest.mark.parametrize(("width", "dpr"), _TIER_POINTS, ids=[f"{w}x{d}" for w, d in _TIER_POINTS])
def test_tiers_and_vw_equal_the_approved_formula(live_browser, hidden_scrollbar_browser, width: int, dpr: float,
                                                 scrollbars: str) -> None:
    browser = hidden_scrollbar_browser if scrollbars == "hidden" else None
    page, context = _open(live_browser, width, dpr, browser=browser)
    try:
        got = page.evaluate(_APPROVED_FORMULA_JS)
        detail = json.dumps(got, sort_keys=True)
        assert got["zoom"] == "1", detail
        assert got["actual"] == got["expected"], detail
        assert abs(got["actualVw"] - got["expectedVw"]) < 1e-6, detail
    finally:
        page.close()
        context.close()


def test_opened_plot_pager_reads_the_authoritative_width(live_browser) -> None:
    """(c) The opened plot's frame/pager geometry is the width the table uses --
    never a zero from a hidden or unrelated DOM probe ()."""
    page, context = _open(live_browser, 1440, 1.0)
    try:
        page.locator("#plot-tbody tr.plot-row td").first.click()
        page.wait_for_selector(".editor-band", timeout=30_000)
        wait_ready(page)
        settle(page)
        got = page.evaluate(
            """() => {
              """ + _MAIN_CONTENT_JS + """
              const frame = document.querySelector('.editor-band');
              return {
                frameWidth: parseFloat((frame.style.getPropertyValue('--eos-frame-width') || '').replace('px', '')),
                mainContent,
              };
            }"""
        )
        assert abs(got["frameWidth"] - got["mainContent"]) <= 1, json.dumps(got, sort_keys=True)
    finally:
        page.close()
        context.close()


def test_card_view_pager_keeps_every_tile_on_one_page(live_browser) -> None:
    """ in the mobile-card tier (1280 CSS px: the table is display:none).
    The card tier is really reached now (the approved tier width), and its plot
    sheet shows every tile of a plot on one page -- the bug's visible symptom
    was the Title tile pushed to a later page. Note: the sheet renders without a
    presentation session (editor.js falls back to all units), so the WIDTH the
    card tier would hand a pager is guarded by test_width_invariants' card-tier
    branch (availableTableWidth() == the board's content box)."""
    page, context = _open(live_browser, 1280, 1.0)
    try:
        assert page.evaluate("document.documentElement.classList.contains('ceres-lw-le-1279')"), "not the card tier"
        cards = page.locator("#plot-card-list .plot-card")
        seen = []
        for index in range(min(12, cards.count())):
            cards.nth(index).click()
            page.wait_for_selector("#mobile-plot-sheet .editor-band .slot-tile-cell", timeout=30_000)
            wait_ready(page)
            settle(page)
            got = page.evaluate(
                """() => {
                  const sheet = document.getElementById('mobile-plot-sheet');
                  return {
                    tiles: sheet.querySelectorAll('.slot-tile-cell').length,
                    pager: sheet.querySelector('.eos-pages span')?.textContent || null,
                  };
                }"""
            )
            seen.append(got)
            page.locator("#mobile-plot-sheet-close").click()
            page.locator("#mobile-plot-sheet .editor-band").wait_for(state="hidden")
            if got["tiles"] >= 2 or got["pager"]:
                break
        # A multi-tile plot whose tiles all fit shows them on ONE page, no pager.
        assert seen and seen[-1]["tiles"] >= 2 and seen[-1]["pager"] is None, json.dumps(seen)
    finally:
        page.close()
        context.close()


_BADGE_LINES_JS = """() => {
  const rows = [...document.querySelectorAll('#plot-tbody tr.plot-row')];
  const heights = rows.map(tr => tr.getBoundingClientRect().height);
  let wrapped = 0, prematureMax = -Infinity, clipped = 0;
  for (const tr of rows) {
    const td = tr.lastElementChild;
    const row = td.querySelector('.badge-row');
    if (!row || !row.childElementCount) continue;
    if (td.scrollWidth > td.clientWidth + 1) clipped += 1;
    const cs = getComputedStyle(td);
    const contentRight = td.getBoundingClientRect().right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
    const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
    const items = [...row.children].map(el => {
      const r = el.getBoundingClientRect();
      return { top: Math.round(r.top), right: r.right, width: r.width };
    });
    const lines = [];
    for (const item of items) {
      const line = lines.find(l => Math.abs(l.top - item.top) <= 2);
      if (line) line.items.push(item); else lines.push({ top: item.top, items: [item] });
    }
    lines.sort((a, b) => a.top - b.top);
    if (lines.length > 1) wrapped += 1;
    for (let k = 0; k + 1 < lines.length; k += 1) {
      // Real empty space after the last badge of a line, minus what the next
      // line's first badge needs: > 0 means it wrapped too early.
      const empty = contentRight - Math.max(...lines[k].items.map(i => i.right));
      prematureMax = Math.max(prematureMax, empty - (lines[k + 1].items[0].width + gap));
    }
  }
  const scroller = document.getElementById('plot-table-scroll');
  return {
    rows: rows.length, wrapped, clipped,
    prematureMax: prematureMax === -Infinity ? null : prematureMax,
    minHeight: Math.min(...heights), maxHeight: Math.max(...heights),
    scrollerCW: scroller.clientWidth, scrollerSW: scroller.scrollWidth,
  };
}"""


def test_badges_wrap_only_when_the_board_is_too_narrow(live_browser) -> None:
    """'s visible symptom (dead space inside the Badges cell) in the
     model: at a real 1440 window every badge row fits on one line (the
    browser gives Badges its content width), so no row wraps and every row has
    the same height -- the virtual scroller's one row height. When the board is
    too narrow for every column's content (forced here: the scroller capped at
    1100 px), Badges -- the only wrap-capable column -- wraps instead of being
    cut off or pushing the table past its scroller, and never wraps early. The
    forced step makes the wrap check non-vacuous."""
    page, context = _open(live_browser, 1440, 1.0)
    try:
        real = page.evaluate(_BADGE_LINES_JS)
        detail = json.dumps(real, sort_keys=True)
        assert real["rows"] > 0, detail
        assert real["wrapped"] == 0 and real["clipped"] == 0, detail
        assert real["maxHeight"] <= real["minHeight"] + 1, detail
        page.add_style_tag(content="#plot-table-scroll { max-inline-size: 1100px; }")
        page.wait_for_function("document.querySelector('#plot-table-scroll').clientWidth <= 1100")
        settle(page)
        got = page.evaluate(_BADGE_LINES_JS)
        detail = json.dumps(got, sort_keys=True)
        assert got["scrollerCW"] <= 1100, detail
        assert got["wrapped"] > 0, detail
        assert got["clipped"] == 0, detail
        assert got["scrollerSW"] <= got["scrollerCW"] + 1, detail
        assert got["prematureMax"] is not None and got["prematureMax"] <= 1, detail
    finally:
        page.close()
        context.close()


_SHOWN_PLOTS_JS = ("() => document.querySelectorAll('#plot-tbody > tr.plot-row').length"
                   " + document.querySelectorAll('#plot-card-list .plot-card').length")

_STAGEABLE_SLOT = (
    '.editor-band [data-slot-action="add"]:not([disabled]), '
    '.editor-band [data-slot-action="replace"]:not([disabled])'
)


def _open_grid_picker(page: Page) -> None:
    rows = page.locator("#plot-tbody tr.plot-row")
    for index in range(min(12, rows.count())):
        cell = rows.nth(index).locator("td").first
        cell.click()
        page.wait_for_selector(".editor-band", timeout=30_000)
        try:
            page.wait_for_selector(_STAGEABLE_SLOT, timeout=8_000)
        except PlaywrightTimeoutError:
            cell.click()
            page.locator(".editor-band").wait_for(state="hidden")
            continue
        page.locator(_STAGEABLE_SLOT).first.click()
        page.wait_for_selector(".candidate-list.is-card-grid > .candidate-grid-card:not(.is-ghost)", timeout=60_000)
        settle(page)
        return
    pytest.fail("no plot with a stageable slot to open the grid picker")


@pytest.mark.parametrize("scrollbars", ("hidden", "painted"))
def test_picker_grid_shows_seven_per_row_at_full_hd(live_browser, hidden_scrollbar_browser, scrollbars: str) -> None:
    """ (maintainer: DPR 1.0, browser zoom 100%, 1920x1080, maximized, with a
    scrollbar -- the picker grid MUST show 7 tiles per row). The grid's column
    count is derived from its own width -- the authoritative content width minus
    the picker margins -- never from a tier breakpoint:
        columns = floor((content + gap) / (card_min + gap))
    with card_min = --candidate-grid-card-min. tracks == that count implies each
    track >= card_min. The page's tier classes must still equal the approved
    formula (finding 1) in the same state."""
    width, dpr = 1920, 1.0
    shared, base_url = live_browser
    browser = hidden_scrollbar_browser if scrollbars == "hidden" else shared
    context = browser.new_context(viewport={"width": width, "height": 1080}, device_scale_factor=dpr)
    context.add_init_script("try { localStorage.setItem('ceres:picker-view:v1:sample01', 'grid'); } catch (e) {}")
    page = context.new_page()
    try:
        page.goto(f"{base_url}/?player=sample01")
        page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=60_000)
        wait_ready(page)
        settle(page)
        _open_grid_picker(page)
        tiers = page.evaluate(_APPROVED_FORMULA_JS)
        assert tiers["actual"] == tiers["expected"], json.dumps(tiers, sort_keys=True)
        got = page.evaluate(
            """async () => {
              const grid = document.querySelector('.candidate-list.is-card-grid');
              const cs = getComputedStyle(grid);
              const tracks = cs.gridTemplateColumns.trim().split(/\\s+/).map(parseFloat).filter(width => width > 0);
              const probe = document.createElement('div');
              probe.style.cssText = 'position:absolute;visibility:hidden;inline-size:var(--candidate-grid-card-min)';
              grid.appendChild(probe);
              const cardMin = probe.offsetWidth;
              probe.remove();
              const gap = parseFloat(cs.columnGap) || 0;
              const content = grid.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
              let spill = 0;
              for (const card of grid.querySelectorAll(':scope > .candidate-grid-card:not(.is-ghost)')) {
                const box = card.getBoundingClientRect();
                for (const el of card.querySelectorAll('*')) {
                  if (el.closest('.candidate-grid-detail')) continue;
                  const r = el.getBoundingClientRect();
                  if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) spill += 1;
                }
              }
              const scale = await import('/static/scale.js');
              return {
                tracks: tracks.length, trackW: tracks[0], cardMin, gap, content,
                expected: Math.min(grid.querySelectorAll(':scope > .candidate-grid-card').length, Math.max(1, Math.floor((content + gap) / (Math.min(content, cardMin) + gap)))),
                spill, gridCW: grid.clientWidth, authoritative: scale.availableTableWidth(),
                tiers: [...document.documentElement.classList].filter(c => c.startsWith('ceres-lw-')).sort(),
                ds: document.documentElement.dataset.ceresScale || '',
              };
            }"""
        )
        detail = json.dumps(got, sort_keys=True)
        assert got["cardMin"] > 0, detail
        # The grid is laid out from the one authoritative width (minus margins).
        assert got["authoritative"] - 32 <= got["gridCW"] <= got["authoritative"] + 1, detail
        assert got["tracks"] == got["expected"], detail
        assert got["spill"] == 0, detail
        assert got["tracks"] == 7, detail
    finally:
        page.close()
        context.close()
