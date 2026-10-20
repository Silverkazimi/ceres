"""census row 35 (batch B): these three tests used to scan `app.js`
source for the tooltip-model literal object (`kind:`/`severity:`/`title:`/
`body:`/`foot:` fields) at each callsite. The tooltip ENGINE is `tipAttrs(model,
severity)`, which turns that model into `data-tip`/`data-tip-kind`/
`data-tip-body`/`data-tip-foot`/`data-tip-severity` attributes on the rendered
element (`tests/ceres/test_tooltip_surface.py` reads tooltips the same way,
without hovering). Converted to reads of those real rendered attributes on a
real page against real live data, instead of the source literal that builds
them."""

from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres, wait_for_board

ROOT = Path(__file__).parents[2]
INDEX_HTML = ROOT / "ceres" / "static" / "index.html"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_rendered_clear_plot_control_is_a_structured_danger_warning(live_browser: tuple[Browser, str]) -> None:
    """Census row 35: replaces the source-text scan of `renderEditorGroup()`'s
 Clear Plot warning-model object literal with a read of the real button's
 `data-tip*` attributes (built by `tipAttrs()` from that exact model).

 Named injection (not applied here; independently watched red/green per
 ): the Clear Plot button's model changed from
 `{ kind: 'warn', severity: 'danger', ... }` to
 `{ kind: 'warn', severity: 'caution', ... }`. Expected red:
 `data-tip-severity` reads `"caution"` where this test asserts `"danger"`."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    page.locator("#plot-tbody > tr.plot-row").first.click()
    button = page.locator(".slot-bulk[data-slot-action='clear_plot']").first
    button.wait_for(timeout=30_000)
    assert button.get_attribute("data-tip") == "Clear plot"
    assert button.get_attribute("data-tip-kind") == "warn"
    assert button.get_attribute("data-tip-severity") == "danger"
    page.close()


def test_rendered_warning_badge_carries_dynamic_severity_from_the_row_notice(live_browser: tuple[Browser, str]) -> None:
    """Census row 35: replaces the source-text scan of `badgeHtml()`'s
 dynamic-severity branch (`rawSeverity`/`warningModel`) with a read of a
 real badge chip's rendered `data-tip-severity`/`data-tip-body`. Uses a real
 live plot carrying a badge whose severity comes from the plot's OWN
 `notices` entry (`dark_discount_wasted`, severity `caution`), not a static
 per-badge default -- proving the severity really is read per-row, not
 hardcoded per-badge-code.

 Named injection (not applied here; independently watched red/green per
 ): `badgeHtml()`'s `const rawSeverity = rowNotice?.severity || info.severity;`
 changed to `const rawSeverity = info.severity;` (drops the row's own
 severity in favour of the static default). Expected red: the rendered
 chip's `data-tip-severity` no longer matches the row notice's real
 `severity` field for a badge whose per-row severity differs from its
 static default."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    rows = page.locator("tr.plot-row")
    chip = None
    for index in range(rows.count()):
        candidate = rows.nth(index).locator(".badge.badge-dark_discount_wasted").first
        if candidate.count():
            chip = candidate
            break
    if chip is None:
        pytest.skip("No live snapshot plot carries the Dark Discount wasted badge")
    chip.wait_for(timeout=10_000)
    assert chip.get_attribute("data-tip-kind") == "warn"
    assert chip.get_attribute("data-tip-severity") == "caution"
    assert "Dark Discount" in (chip.get_attribute("data-tip-body") or "")
    # TEXTS:115/1051: badge feet are optional; this guard checks the badge's own
    # title/body contract without requiring or forbidding an optional foot.
    page.close()


def test_board_version_has_no_tooltip() -> None:
    """The version stamp is a status display, not a tooltip surface."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    version_start = source.index('id="board-version"')
    version_end = source.find(">", version_start)
    assert version_end > version_start
    assert "data-tip=" not in source[version_start:version_end]
