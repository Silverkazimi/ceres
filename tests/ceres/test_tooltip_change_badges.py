import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.ceres.conftest import spawn_live_ceres


PROJECTED_FOOT = "Projected from your staged changes — not yet on chain."
UNKNOWN_FOOT = "Not calculated — depends on which staged card absorbs the amount above the cap."
# The fourth badge (the maintainer's "change to the final production") is a Now/After list
# whose foot states how it is estimated (editor.js renderChangeBadges).
PRODUCTION_FOOT_PREFIX = "Estimate: output follows the Total PP change ("


def test_change_badges_have_structured_tooltips_and_leave_row_50_alone():
    # INJECTION: renderChangeBadges() emits legacy title/delta tooltip strings or adds tooltip keys to row 50.
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page()
        try:
            page.goto(base_url, wait_until="networkidle")
            rows = page.locator("#plot-tbody > tr.plot-row")
            if rows.count() == 0:
                pytest.skip("Missing snapshot state: selector '#plot-tbody > tr.plot-row' matched no plot rows")
            selected = False
            for index in range(rows.count()):
                row = rows.nth(index)
                row.click()
                expanded = page.locator("tr.expand-row").nth(index)
                try:
                    expanded.wait_for(state="attached", timeout=5_000)
                    # the band renders `preparing` first; tiles and their remove buttons
                    # exist once it reports `ready`
                    expanded.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
                except PlaywrightTimeoutError:
                    row.click()
                    continue
                remove = expanded.locator(".slot-group[data-group-id='worker'] .slot-remove").first
                if remove.count() == 0:
                    row.click()
                    continue
                remove.evaluate("element => element.click()")
                badges = page.locator(".editor-change-badge")
                try:
                    badges.first.wait_for(state="attached", timeout=5_000)
                    selected = True
                    break
                except PlaywrightTimeoutError:
                    row.click()
            if not selected:
                pytest.fail("Snapshot rows matched '#plot-tbody > tr.plot-row', but no rendered '.editor-change-badge' was produced")

            badges = page.locator(".editor-change-badge")
            assert badges.count() > 0
            for index in range(badges.count()):
                badge = badges.nth(index)
                # structured: a headline key, or (the production badge) a Now/After item list
                keys = badge.evaluate("element => element.getAttributeNames().filter(name => name === 'data-tip-key').length")
                items = badge.get_attribute("data-tip-items")
                assert keys == 1 or (keys == 0 and items and "Now" in items and "After" in items), badge.inner_text()
                assert badge.get_attribute("data-tip-body") is not None
                foot = badge.get_attribute("data-tip-foot")
                tip = badge.get_attribute("data-tip") or ""
                assert not tip[:1].isdigit()
                assert not tip.startswith(("$", "€", "£", "¥"))
                assert not tip.startswith("Exact")
                # TEXTS:115/1051: a badge foot is optional; validate it only when
                # the selected live model supplies one.
                if foot is not None:
                    assert foot in (PROJECTED_FOOT, UNKNOWN_FOOT) or (items and foot.startswith(PRODUCTION_FOOT_PREFIX)), foot

            base_badge = page.locator(".editor-change-badge").filter(has_text="Total Base").first
            assert base_badge.get_attribute("data-tip-key") != base_badge.inner_text()
            # Row 50 (the per-card footer cells) now names its own figure as the tooltip
            # headline (editor.js editorBasePpColumn: `key: figure`); it must never carry a
            # change-badge value, only the figure the cell shows.
            cells = page.locator(".editor-base-pp-cell[data-tip-key]")
            for index in range(cells.count()):
                cell = cells.nth(index)
                assert cell.get_attribute("data-tip-key") == cell.locator("b").inner_text().strip()
        finally:
            page.close()
