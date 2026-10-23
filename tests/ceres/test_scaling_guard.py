""" /  /  /  / : scaling guard.

Owner (): "highest priority is the full resolution of scaling, all browser
widths must work equally, each scaling must work, no empty space, all nicely
filled out". Owner (): browser zoom as a layout mechanism is forbidden;
"the table must scale to all sizes gracefully". Owner (): "1/3 of the
page is empty space in the badge column."

 forbids this guard from opening Chromium windows on the maintainer's desktop.
It therefore runs headless by default, with each case's CSS viewport and DPR set
on its browser context. Headless Chromium otherwise paints no classic scrollbar,
so a test-only init style reserves a 15px always-present gutter; the guard asserts
that the measured gutter is 14-18px before making any layout measurements. Run:
  env -u DISPLAY -u WAYLAND_DISPLAY python3 -m pytest -q tests/ceres/test_scaling_guard.py

The guarded headless Chromium context is measured at:
  1526x730 @1.25  headline: the maintainer's own browser (Full HD at Windows 125%)
  1280x720 @1.5   Full HD at 150% -- the phone/card presentation (content <= 1279)
  1366x768 @1, 1440x900 @1, 1536x864 @1.25, 1920x1080 @1, 2560x1440 @1

Table assertions -- each column's width against its CONTENT need (the defect
class: one column swallowing the slack while table width == column sum, which
the  guard's `colSum >= scrollerCW` check accepted by construction):
  content_i  = the widest visible content of column i (header or any mounted
               row) + cell padding; Badges = its badge items + gaps + padding;
  slack      = table width - sum(content);  fair_i = slack * content_i / sum(content)
  (a) every column (Badges included): width_i <= content_i + 1.5 * fair_i + 4px
  (b) no column takes more than X = 25% of the table unless its content needs
      it: share_i <= max(25%, 1.15 * content_i / sum(content)). X = 25%: 16
      columns (even share 6.25%); the widest content column (Badges) needs
      19-25% of the table at the desktop widths above, so 25% flags a column that
      holds more than a quarter of the row without the content to show for it.
  (c) growth spread: max(width/content) / min(width/content) <= 1.6
  plus: the table spans its container, no clipped cell or header, and no side
  scrolling (page or table). These table checks remain hard failures.
 fluid-layout checks (hard failures since the rework landed): NO PAGE ZOOM
(computed root zoom == 1), and the picker (worker picker grid, opened on a real
plot) filling its row:
  cardMin = clamp(13.5rem, 15.4cqi, 15.5rem) of the picker slice;
  columns == floor((gridContent + gap) / (cardMin + gap))
  and cardMin <= max(216px, 0.16 * gridContent) + 1 (the minimum scales with
  the grid -- e.g. 6 per row at a ~1478 px grid, not 5).
Phone/tablet tier (1280x720@1.5 is the card view BY DESIGN, content <= 1279):
the picker opens in the mobile plot sheet, whose column count belongs to the
mobile branch ( phase E rule: 3 columns, 2 at <= 767, 1 at <= 544;
 will set 2 cards side by side on phones). There the guard asserts that
rule's count, that the tracks fill the grid row, no card spills and no side
scrolling -- not the desktop auto-fill count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .conftest import settle, spawn_live_ceres, wait_ready
from .table_edge import OVERHANG_JS, TABLE_EXTRA_WIDTH_PX, TABLE_OVERHANG_PX, overhang_problems


CASES = [
    ("1526x730@1.25-headline", 1526, 730, 1.25),
    ("1280x720@1.5", 1280, 720, 1.5),
    ("1366x768@1", 1366, 768, 1.0),
    ("1440x900@1", 1440, 900, 1.0),
    ("1536x864@1.25", 1536, 864, 1.25),
    ("1920x1080@1", 1920, 1080, 1.0),
    ("2560x1440@1", 2560, 1440, 1.0),
]
FAIR_FACTOR = 1.5
FAIR_TOLERANCE_PX = 4
MAX_SHARE = 0.25
MAX_SPREAD = 1.6


_CLASSIC_SCROLLBAR_INIT = """(() => {
  const style = document.createElement('style');
  style.id = 'ceres-s9380-classic-scrollbar';
  style.textContent = `
    html { overflow-y: scroll !important; scrollbar-gutter: stable !important; }
    ::-webkit-scrollbar { width: 15px !important; }
  `;
  document.documentElement.appendChild(style);
})();"""


def _wslg_display_active() -> bool:
    display = os.environ.get("DISPLAY", "")
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    return (
        display in {":0", ":0.0"}
        or wayland_display == "wayland-0"
        or Path("/mnt/wslg").exists()
    )


def _headless_mode() -> bool:
    if os.environ.get("CERES_GUARD_HEADED") != "1":
        return True
    if os.environ.get("CERES_GUARD_ALLOW_HEADED") != "1":
        pytest.fail(
            ": refusing to open a visible Chromium window; "
            "CERES_GUARD_ALLOW_HEADED=1 is required"
        )
    if _wslg_display_active():
        pytest.fail(": refusing to open a visible Chromium window on WSLg")
    return False


@pytest.fixture(scope="module")
def live_server() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _launch(live_server, width: int, height: int, dpr: float):
    shared, base_url = live_server
    # Headless Chromium passes --hide-scrollbars by default; drop it so the
    # reserved classic gutter is real (the conftest launcher does the same).
    browser = shared.browser_type.launch(headless=_headless_mode(), ignore_default_args=["--hide-scrollbars"])
    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=dpr,
    )
    context.add_init_script(_CLASSIC_SCROLLBAR_INIT)
    context.add_init_script("try { localStorage.setItem('ceres:picker-view:v1:player0005', 'grid'); } catch (e) {}")
    page = context.new_page()
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0"
        " || document.querySelectorAll('#plot-card-list .plot-card').length > 0",
        timeout=90_000,
    )
    wait_ready(page)
    settle(page)
    return browser, page


# All widths are rect px of the rendered page (on the pre- tree a root
# zoom scales every rect by the same factor, so every ratio below is exact and
# the px tolerances scale with it via `f`).
_COLUMNS_JS = """() => {
  const f = window.ceresScale?.factor || 1;
  const scroller = document.getElementById('plot-table-scroll');
  const row = document.querySelector('#plot-tbody tr.plot-row');
  const heads = [...document.querySelectorAll('#table-head-sticky thead th, thead#table-head-sticky th')];
  const cells = [...row.children];
  const widths = cells.map(td => td.getBoundingClientRect().width);
  // review fix: the Wood/hr, Stone/hr, Iron/hr columns hide below 1760px
  // (`html.ceres-lw-le-1759 .col-w-wood/-stone/-iron { display: none }`, raised from
  // 1680 by 's inline sort chevron) -- a hidden
  // column is not a fairness defect and must not enter any ratio, so it is excluded
  // entirely below rather than measured (its width is legitimately 0; other columns
  // absorb its share). display:none can leave computed padding non-zero (the property
  // is unaffected by display), so this checks both signals, not width alone.
  const hidden = cells.map((td, i) => getComputedStyle(td).display === 'none' || widths[i] === 0);
  const growIdx = cells.length - 1;  // Badges is the last column
  // the last column (DEC stake) reserves extra padding-inline-end for the
  // slide-in badge bar behind it (`--table-end-reserve`, ) -- dead space, not
  // the column claiming more than its fair share. Measured as the difference between
  // its own padding-inline-end and a normal (non-last) cell's, in px, so the fairness
  // rule (a) below can subtract it from the DEC column's measured width.
  const tableEndReserve = cells.length > 1
    ? Math.max(0, (parseFloat(getComputedStyle(cells[cells.length - 1]).paddingInlineEnd) -
        parseFloat(getComputedStyle(cells[cells.length - 2]).paddingInlineEnd)) * f)
    : 0;
  const pad = (el) => {
    const cs = getComputedStyle(el);
    return (parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)
      + parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth)) * f;
  };
  // Out-of-flow overlays (a positioned build mark, a visually-hidden label)
  // take no column width -- they are not content the column must hold.
  const outOfFlow = (el, cell) => {
    for (let x = el; x && x !== cell; x = x.parentElement) {
      const p = getComputedStyle(x).position;
      if (p === 'absolute' || p === 'fixed') return true;
    }
    return false;
  };
  const extent = (cell) => {
    let lo = Infinity, hi = -Infinity;
    const add = (r) => { if (r.width > 1 && r.height > 1) { lo = Math.min(lo, r.left); hi = Math.max(hi, r.right); } };
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (outOfFlow(n.nodeType === Node.TEXT_NODE ? n.parentElement : n, cell)) continue;
      if (n.nodeType === Node.TEXT_NODE) {
        if (!n.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const r of range.getClientRects()) add(r);
      } else if (!n.firstElementChild && !n.textContent.trim()) {
        add(n.getBoundingClientRect());
      }
    }
    return hi > lo ? hi - lo : 0;
  };
  const badgeContent = (td) => {
    const badgeRow = td.querySelector('.badge-row');
    if (!badgeRow || !badgeRow.childElementCount) return 0;
    const gap = (parseFloat(getComputedStyle(badgeRow).columnGap) || 0) * f;
    const items = [...badgeRow.children].map(el => el.getBoundingClientRect().width);
    return items.reduce((a, b) => a + b, 0) + gap * (items.length - 1);
  };
  const content = widths.map(() => 0);
  heads.forEach((th, i) => { if (i < content.length) content[i] = Math.max(content[i], extent(th) + pad(th)); });
  const rows = [...document.querySelectorAll('#plot-tbody tr.plot-row')];
  for (const tr of rows) {
    [...tr.children].forEach((td, i) => {
      const w = i === growIdx ? badgeContent(td) : extent(td);
      content[i] = Math.max(content[i], w + pad(td));
    });
  }
  // A hidden column's computed padding is unaffected by display:none, so its measured
  // "content" above can be a nonzero phantom (padding alone) even though it renders
  // nothing -- zero it here so it can never inflate total_c against total_w.
  hidden.forEach((h, i) => { if (h) content[i] = 0; });
  const clippedCells = rows.flatMap(tr => [...tr.children]).filter(td => td.scrollWidth > td.clientWidth + 1).length;
  const clippedHead = heads.filter(th => th.scrollWidth > th.clientWidth + 1).length;
  const doc = document.documentElement;
  return {
    f, inner: innerWidth, client: doc.clientWidth, dpr: devicePixelRatio, zoom: getComputedStyle(doc).zoom,
    labels: heads.map(th => (th.getAttribute('aria-label') || th.textContent).trim().slice(0, 12)),
    widths: widths.map(w => Math.round(w * 10) / 10), content: content.map(w => Math.round(w * 10) / 10),
    growIdx, tableEndReserve, hidden, rows: rows.length, container: scroller.getBoundingClientRect().width,
    scrollerCW: scroller.clientWidth, scrollerSW: scroller.scrollWidth,
    docCW: doc.clientWidth, docSW: doc.scrollWidth, clippedCells, clippedHead,
    overhang: (""" + OVERHANG_JS + """)(),
  };
}"""

_PICKER_JS = """() => {
  const grid = [...document.querySelectorAll('.candidate-list.is-card-grid')].find(g => g.getClientRects().length);
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
  const doc = document.documentElement;
  const cls = doc.classList;
  return {
    phoneCols: 2,
    trackSum: tracks.reduce((a, b) => a + b, 0),
    tracks: tracks.length, minTrack: Math.min(...tracks), cardMin, gap, content,
    fits: Math.min(grid.querySelectorAll(':scope > .candidate-grid-card').length, Math.max(1, Math.floor((content + gap) / (Math.min(content, cardMin) + gap)))),
    scaledMin: Math.max(216, 0.16 * content), spill, docCW: doc.clientWidth, docSW: doc.scrollWidth,
  };
}"""

_STAGEABLE = '[data-slot-action="add"]:not([disabled]), [data-slot-action="replace"]:not([disabled])'


def _open_picker(page: Page, table: bool) -> None:
    opener = page.locator("#plot-tbody tr.plot-row td:first-child" if table else "#plot-card-list .plot-card")
    scope = ".editor-band" if table else "#mobile-plot-sheet .editor-band"
    for index in range(min(12, opener.count())):
        opener.nth(index).click()
        page.wait_for_selector(scope, timeout=30_000)
        try:
            page.wait_for_selector(f"{scope} :is({_STAGEABLE})", timeout=8_000)
        except PlaywrightTimeoutError:
            if table:
                opener.nth(index).click()
            else:
                page.locator("#mobile-plot-sheet-close").click()
            settle(page)
            continue
        page.locator(f"{scope} :is({_STAGEABLE})").first.click()
        page.wait_for_selector(".candidate-list.is-card-grid > .candidate-grid-card:not(.is-ghost)", timeout=60_000)
        settle(page)
        return
    pytest.fail("no plot with a stageable slot to open the picker")


def _check_columns(got: dict) -> list[str]:
    failures = []
    widths, content = got["widths"], got["content"]
    # review fix: a display:none column (e.g. Wood/hr below 1680px) is legitimately
    # not part of the layout -- it is excluded from every sum, ratio and check below, not
    # measured as a fairness defect (its own width IS 0, correctly; without this it produced
    # a `w / c == 0` ratio that crashed the growth-spread check with a ZeroDivisionError the
    # moment ANY column was hidden). `_COLUMNS_JS` already zeroes a hidden column's `content`.
    hidden = got.get("hidden") or [False] * len(widths)
    # `--table-end-reserve` () is dead space reserved for the badge bar
    # inside the last column's (DEC stake) own end padding -- real, but not the column
    # unfairly claiming slack. Rule (a) below reads this reduced width for the last
    # column only; every other rule (share, spread, container span) keeps the real width.
    reserve = got.get("tableEndReserve", 0.0)
    total_w = sum(w for w, h in zip(widths, hidden) if not h)
    total_c = sum(c for c, h in zip(content, hidden) if not h)
    slack = max(0.0, total_w - total_c)
    tol = FAIR_TOLERANCE_PX * got["f"]
    ratios = []
    for i, (w, c) in enumerate(zip(widths, content)):
        if i < len(hidden) and hidden[i]:
            continue
        label = got["labels"][i] if i < len(got["labels"]) else str(i)
        if c <= 0:
            failures.append(f"{label}: no measurable content")
            continue
        fair = slack * c / total_c
        rule_a_w = w - reserve if i == got["growIdx"] else w
        if rule_a_w > c + FAIR_FACTOR * fair + tol:
            kind = "BADGES" if i == got["growIdx"] else "column"
            failures.append(f"{kind} {label}: {rule_a_w:.0f}px > content {c:.0f} + 1.5 x fair share {fair:.0f}")
        share, need = w / total_w, c / total_c
        if share > max(MAX_SHARE, 1.15 * need):
            failures.append(f"{label}: {share:.1%} of the table (content needs {need:.1%})")
        ratios.append(w / c)
    if ratios and max(ratios) / min(ratios) > MAX_SPREAD:
        failures.append(f"growth spread {max(ratios) / min(ratios):.2f} > {MAX_SPREAD}")
    # The table spans its container plus the deliberate 8px edge budget whose
    # clipped strip is the last column's reserve padding (table_edge.py).
    if abs(total_w - got["container"] - TABLE_EXTRA_WIDTH_PX) > 2 * got["f"]:
        failures.append(f"table {total_w:.0f}px does not span its container {got['container']:.0f}px"
                        f" + the {TABLE_EXTRA_WIDTH_PX}px edge budget")
    if got["clippedCells"] or got["clippedHead"]:
        failures.append(f"clipped cells {got['clippedCells']}, clipped headers {got['clippedHead']}")
    if got["docSW"] > got["docCW"] + 1 or got["scrollerSW"] > got["scrollerCW"] + TABLE_OVERHANG_PX + 1:
        failures.append(f"side scrolling: page {got['docSW']}/{got['docCW']}, table {got['scrollerSW']}/{got['scrollerCW']}")
    failures += overhang_problems(got.get("overhang") or {})
    return failures


def _check_picker(grid: dict, phone: bool = False) -> list[str]:
    failures = []
    if phone:
        filled = grid["trackSum"] + grid["gap"] * (grid["tracks"] - 1)
        if grid["tracks"] != grid["phoneCols"]:
            failures.append(f"phone picker shows {grid['tracks']} per row, the mobile rule says {grid['phoneCols']}")
        if abs(filled - grid["content"]) > 1:
            failures.append(f"phone picker tracks fill {filled:.0f}px of a {grid['content']:.0f}px row")
        if grid["spill"]:
            failures.append(f"phone picker cards spill: {grid['spill']}")
        if grid["docSW"] > grid["docCW"] + 1:
            failures.append(f"side scrolling with the phone picker open: {grid['docSW']}/{grid['docCW']}")
        return failures
    if grid["tracks"] != grid["fits"]:
        failures.append(f"picker shows {grid['tracks']} per row, {grid['fits']} fit")
    if grid["cardMin"] > grid["scaledMin"] + 1:
        failures.append(f"picker card minimum {grid['cardMin']}px does not scale (<= {grid['scaledMin']:.0f}px" f" at a {grid['content']:.0f}px grid)")
    if grid["minTrack"] < grid["cardMin"] - 0.5 or grid["spill"]:
        failures.append(f"picker cards cramped: track {grid['minTrack']:.0f} < {grid['cardMin']}, spill {grid['spill']}")
    if grid["docSW"] > grid["docCW"] + 1:
        failures.append(f"side scrolling with the picker open: {grid['docSW']}/{grid['docCW']}")
    return failures


@pytest.fixture(scope="module", params=CASES, ids=[c[0] for c in CASES])
def real_window_measurement(live_server, request):
    name, width, height, dpr = request.param
    browser, page = _launch(live_server, width, height, dpr)
    try:
        gutter = page.evaluate("innerWidth - document.documentElement.clientWidth")
        assert 14 <= gutter <= 18, (
            f"{name}: expected a 14-18px classic scrollbar gutter, got {gutter}px"
        )
        table = page.evaluate(
            "(() => { const s = document.getElementById('plot-table-scroll');"
            " return !!s && getComputedStyle(s).display !== 'none'; })()"
        )
        report: dict = {"case": name, "table": table}
        column_failures: list[str] = []
        zoom_failures: list[str] = []
        # no page zoom -- the page lays out at the browser's own CSS width.
        zoom = page.evaluate("getComputedStyle(document.documentElement).zoom")
        report["zoom"] = zoom
        if float(zoom or 1) != 1:
            zoom_failures.append(f"page zoom {zoom} ( forbids zoom-based scaling)")
        if table:
            got = page.evaluate(_COLUMNS_JS)
            report["columns"] = got
            column_failures += _check_columns(got)
        else:
            got = page.evaluate(
                "({cards: document.querySelectorAll('#plot-card-list .plot-card').length, inner: innerWidth,"
                " client: document.documentElement.clientWidth, docSW: document.documentElement.scrollWidth,"
                " phone: document.documentElement.classList.contains('ceres-lw-le-1279'),"
                " zoom: getComputedStyle(document.documentElement).zoom})"
            )
            report["cards"] = got
            if not (got["phone"] and got["cards"] > 0 and got["client"] <= 1279):
                column_failures.append(f"card view outside the phone switch: {got}")
            if got["docSW"] > got["client"] + 1:
                column_failures.append(f"side scrolling in the card view: {got['docSW']}/{got['client']}")
        measurement = {
            "name": name,
            "page": page,
            "table": table,
            "report": report,
            "column_failures": column_failures,
            "zoom_failures": zoom_failures,
        }
        yield measurement
    finally:
        browser.close()


def _assert_no_failures(name: str, failures: list[str], report: dict) -> None:
    print(json.dumps(report, sort_keys=True))
    assert not failures, f"{name}: " + "; ".join(failures) + "\n" + json.dumps(report, sort_keys=True)


def test_real_window_fills_without_slack(real_window_measurement) -> None:
    measurement = real_window_measurement
    _assert_no_failures(measurement["name"], measurement["column_failures"], measurement["report"])


def test_no_page_zoom(real_window_measurement) -> None:
    measurement = real_window_measurement
    _assert_no_failures(measurement["name"], measurement["zoom_failures"], measurement["report"])


def test_picker_fills_row(real_window_measurement) -> None:
    measurement = real_window_measurement
    _open_picker(measurement["page"], measurement["table"])
    grid = measurement["page"].evaluate(_PICKER_JS)
    measurement["report"]["picker"] = grid
    _assert_no_failures(measurement["name"], _check_picker(grid, phone=not measurement["table"]), measurement["report"])
