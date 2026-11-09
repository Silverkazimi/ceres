""" cluster B, signature S1: diagnostic only, never fails.

Prints one `S1DIAG ` JSON line per viewport (greppable in the CI log) with the numbers that separate a CI font/environment
cause from product width growth: table and scroller widths, every header cell width, the widest body columns, the table's
computed font stack, the concrete font Chromium rendered (CDP CSS.getPlatformFontsForNode), fc-match of the generic and
first named families, and the Chromium version.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres, wait_ready

VIEWPORTS = [(1290, 800), (1296, 800), (1366, 800)]

_MEASURE_JS = """
(() => {
  const table = document.getElementById('plot-table');
  const scroller = document.getElementById('plot-table-scroll');
  if (!table || !scroller) return {error: 'no table or scroller'};
  const cs = getComputedStyle(table);
  const ths = [...table.querySelectorAll('thead th')];
  const rows = [...table.querySelectorAll('tbody tr.plot-row')];
  const colMax = [];
  rows.forEach(tr => [...tr.children].forEach((td, i) => { colMax[i] = Math.max(colMax[i] || 0, td.offsetWidth); }));
  const widest = colMax.map((w, i) => ({col: i, w})).sort((a, b) => b.w - a.w).slice(0, 6);
  return {
    tableScrollWidth: table.scrollWidth, tableOffsetWidth: table.offsetWidth,
    tableRectWidth: table.getBoundingClientRect().width,
    scrollerClientWidth: scroller.clientWidth, scrollerScrollWidth: scroller.scrollWidth,
    tiers: [...document.documentElement.classList].filter(c => c.startsWith('ceres-lw-')).sort(),
    headers: ths.map(th => ({text: th.textContent.trim(), w: th.offsetWidth})),
    widestTd: widest, rowCount: rows.length,
    fontFamily: cs.fontFamily, fontSize: cs.fontSize, bodyFontFamily: getComputedStyle(document.body).fontFamily,
  };
})()
"""


def _fc_match(pattern: str) -> str:
    try:
        out = subprocess.run(["fc-match", pattern, "family", "file"], capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip()
    except Exception as exc:  # diagnostic: never fail
        return f"fc-match failed: {exc!r}"


def _first_family(stack: str) -> str:
    first = (stack or "").split(",")[0].strip().strip("\"'")
    return first or "sans-serif"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


@pytest.mark.parametrize(("width", "height"), VIEWPORTS, ids=[f"{w}x{h}" for w, h in VIEWPORTS])
def test_plot_table_width_diagnostic(live_browser, capsys, width: int, height: int) -> None:
    browser, base_url = live_browser
    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1.0)
    context.add_init_script("try { localStorage.setItem('ceres:picker-view:v1:sample01', 'grid'); } catch (e) {}")
    page = context.new_page()
    report: dict = {"viewport": f"{width}x{height}", "chromium": browser.version}
    try:
        page.goto(f"{base_url}/?player=sample01")
        page.wait_for_function(
            "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0"
            " || document.querySelectorAll('#plot-card-list .plot-card').length > 0",
            timeout=60_000,
        )
        wait_ready(page)
        settle(page)
        report.update(page.evaluate(_MEASURE_JS))
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("DOM.enable")
            cdp.send("CSS.enable")
            root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
            node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "#plot-tbody tr.plot-row > td"})["nodeId"]
            report["platformFonts"] = cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node}).get("fonts")
            node_h = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "#plot-table thead th"})["nodeId"]
            report["platformFontsHeader"] = cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node_h}).get("fonts")
        except Exception as exc:
            report["cdpError"] = repr(exc)
        first = _first_family(report.get("fontFamily", ""))
        report["fcMatchSansSerif"] = _fc_match("sans-serif")
        report["fcMatchFirstFamily"] = {first: _fc_match(first)}
        report["fcMatchSystemUi"] = _fc_match("system-ui")
        report["fcMatchInter"] = _fc_match("Inter")
    except Exception as exc:  # diagnostic: never fail
        report["error"] = repr(exc)
    finally:
        with capsys.disabled():
            print("S1DIAG " + json.dumps(report, sort_keys=True), flush=True)
        page.close()
        context.close()
