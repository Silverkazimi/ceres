import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.ceres.conftest import spawn_live_ceres


PROJECTED_FOOT = "Projected from your staged changes — not yet on chain."
UNKNOWN_FOOT = "Not calculated — depends on which staged card absorbs the amount above the cap."


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
                assert badge.evaluate("element => element.getAttributeNames().filter(name => name === 'data-tip-key').length") == 1
                assert badge.get_attribute("data-tip-body") is not None
                foot = badge.get_attribute("data-tip-foot")
                tip = badge.get_attribute("data-tip") or ""
                assert not tip[:1].isdigit()
                assert not tip.startswith(("$", "€", "£", "¥"))
                assert not tip.startswith("Exact")
                # TEXTS:115/1051: a badge foot is optional; validate it only when
                # the selected live model supplies one.
                if foot is not None:
                    assert foot in (PROJECTED_FOOT, UNKNOWN_FOOT)

            base_badge = page.locator(".editor-change-badge").filter(has_text="Base").first
            assert base_badge.get_attribute("data-tip-key") != base_badge.inner_text()
            assert page.locator(".editor-base-pp-cell[data-tip-key]").count() == 0
        finally:
            page.close()
