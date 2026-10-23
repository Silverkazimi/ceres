"""Rendered right-edge badge-handle guard for """

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

from .conftest import spawn_live_ceres


def _center(rect: dict[str, float]) -> float:
    return rect["x"] + rect["width"] / 2


def _rect(page: Page, selector: str) -> dict[str, float]:
    return page.locator(selector).evaluate("el => el.getBoundingClientRect().toJSON()")


def test_rendered_badge_handle_guard() -> None:
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1536, "height": 730})
        try:
            page.goto(f"{base_url}/?player=sample01", wait_until="networkidle")
            page.locator("#plot-table").wait_for(timeout=30_000)
            page.locator("#wallet-edge-tab img").wait_for(timeout=30_000)

            flag = _rect(page, "#badge-flag")
            wallet = _rect(page, "#wallet-edge-tab")
            iris = _rect(page, ".theme-picker")
            for name, rect in (("wallet", wallet), ("Iris", iris)):
                assert abs(_center(rect) - _center(flag)) <= 1, (
                    f"{name} handle centre {_center(rect)} must match flag {_center(flag)}"
                )
                assert abs(rect["width"] - flag["width"]) <= 1, (
                    f"{name} handle width {rect['width']} must match flag {flag['width']}"
                )

            wallet_image = _rect(page, "#wallet-edge-tab img")
            assert abs(_center(wallet_image) - _center(wallet)) <= 1
            glyph = page.locator("#badge-bar .badge-bar-row .badge-status-glyph").first
            glyph.wait_for(timeout=30_000)
            row = glyph.locator("xpath=ancestor::div[contains(@class, 'badge-bar-row')]")
            row_rect = row.bounding_box()
            glyph_rect = glyph.bounding_box()
            assert row_rect and glyph_rect
            assert abs((glyph_rect["x"] + glyph_rect["width"] / 2) - (row_rect["x"] + row_rect["width"] / 2)) <= 1
            assert page.locator(".badge-dot").count() == 0

            page.mouse.move(0, 0)
            page.locator("#badge-flag").hover()
            assert page.locator("#badge-bar").evaluate("el => getComputedStyle(el).boxShadow") == "none"
            assert page.locator("#badge-flag").evaluate("el => getComputedStyle(el).boxShadow") == "none"
        finally:
            page.close()
    css = (Path(__file__).resolve().parents[2] / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
    assert ".badge-status-glyph.is-pending { animation: badge-clock-pulse" in css
    assert ".badge-status-glyph.is-pending { animation: none; }" in css
    assert "badge-golden-pulse" not in css
