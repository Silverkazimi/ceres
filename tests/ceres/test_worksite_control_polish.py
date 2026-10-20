"""Worksite scene control polish (post-/ maintainer feedback, 2026-08-27):
pointer and keyboard interaction with the footer's "Change Worksite" button,
removal of the (Checkout-superseded) per-row "Review -> Sign -> Verify" button,
band-height invariance after that removal, and the horizontal-scrim removal in
`.editor-scene::after`. Live Chromium coverage (spawn_live_ceres) for the DOM/
pointer-behaviour guards, plus two CSS-source guards for the scrim and the
min-height reservation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres

CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"
# A live plot with a rendered worksite building, from player0005's cached snapshot
# same deed used throughout this polish pass's own manual verification.
DEED = "I-290-d05b84f1965811"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _band_page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.wait_for_selector("tr.plot-row")
    row = page.locator(f'tr.plot-row[data-deed-uid="{DEED}"]')
    if row.count() == 0:
        pytest.skip(f"{DEED} not present in this account's live snapshot")
    row.click()
    band = page.locator(f'tr.expand-row[data-deed-uid="{DEED}"] .editor-band')
    band.wait_for(timeout=10_000)
    return page


def test_worksite_change_button_is_pointer_interactive(live_browser: tuple[Browser, str]) -> None:
    """MUTATION EVIDENCE: Injecting `pointer-events: none` on the footer button makes `button.click()` raise `TimeoutError: Locator.click: Timeout 5000ms exceeded` before the modal becomes visible."""
    page = _band_page(live_browser)
    button = page.locator(f'tr.expand-row[data-deed-uid="{DEED}"] button.worksite-change-btn')
    button.wait_for(timeout=5_000)
    assert button.is_visible()
    assert button.is_enabled()
    button.click()
    modal = page.locator("#worksite-modal-overlay")
    modal.wait_for(state="visible", timeout=5_000)
    assert modal.is_visible()
    assert modal.get_by_role("dialog").get_by_role("heading", name="Change worksite").is_visible()
    page.keyboard.press("Escape")
    page.close()


def test_worksite_change_button_keyboard_path_still_works(live_browser: tuple[Browser, str]) -> None:
    """MUTATION EVIDENCE: Removing the footer button's delegated modal-open action makes `modal.wait_for(state="visible")` raise `TimeoutError` after Enter."""
    page = _band_page(live_browser)
    button = page.locator(f'tr.expand-row[data-deed-uid="{DEED}"] button.worksite-change-btn')
    button.wait_for(timeout=5_000)
    button.focus()
    assert button.evaluate("element => document.activeElement === element")
    page.keyboard.press("Enter")
    modal = page.locator("#worksite-modal-overlay")
    modal.wait_for(state="visible", timeout=5_000)
    assert modal.is_visible()
    assert modal.get_by_role("dialog").get_by_role("heading", name="Change worksite").is_visible()
    page.close()


def test_review_sign_verify_button_is_gone(live_browser: tuple[Browser, str]) -> None:
    """Superseded by the portfolio Checkout panel (renderCheckoutSurface() /
 requestOperationPreview({scope: 'portfolio'})), which is not gated on having 2+
 plots staged -- it covers a single staged plot just as well.

 MUTATION-TESTED: re-inserting a `<button class="slot-review">` into the footer
 template in `renderSlotEditor()` and rerunning this test turns it red (count 1,
 not 0). Verified by hand: injected, watched red, reverted, watched green."""
    page = _band_page(live_browser)
    footer = page.locator(f'tr.expand-row[data-deed-uid="{DEED}"] .slot-editor-footer')
    footer.wait_for(timeout=5_000)
    assert footer.locator(".slot-review").count() == 0
    page.close()


def test_band_height_is_unaffected_by_an_empty_footer(live_browser: tuple[Browser, str]) -> None:
    """MUTATION-TESTED: removing `.slot-editor-footer`'s `min-height: 1.7625rem;` and
 rerunning this test turns it red -- an empty footer (no staged moves, no notices,
 the common case on a cold page load) then collapses to just its border+padding
 (~2.6px), shrinking `.editor-band` by ~28px and re-cropping the registered
 worksite art. Verified by hand: injected, watched red (footer_height ~2.6,
 assertion >= 28 failed), reverted, watched green."""
    page = _band_page(live_browser)
    band = page.locator(f'tr.expand-row[data-deed-uid="{DEED}"] .editor-band')
    height = band.evaluate("el => el.getBoundingClientRect().height")
    footer = page.locator(f'tr.expand-row[data-deed-uid="{DEED}"] .slot-editor-footer')
    footer_height = footer.evaluate("el => el.getBoundingClientRect().height")
    # This deed loads cold with nothing staged, so the footer's only content is
    # whatever `min-height` reserves for it -- exactly the case the reservation exists for.
    assert footer_height >= 28, f"footer collapsed to {footer_height}px -- band height is no longer reserved"
    assert height > 250
    page.close()


def test_horizontal_scrim_removed_vertical_scrim_kept() -> None:
    """MUTATION-TESTED: re-adding the `linear-gradient(90deg, ...)` layer to
 `.editor-scene::after` and rerunning this test turns it red on the `not in`
 assertion. Verified by hand: injected, watched red, reverted, watched green."""
    source = CSS.read_text(encoding="utf-8")
    # Two rules share the `.editor-scene::after` selector (one compound with `::before`
    # for shared resets, one standalone for the actual background) -- match the
    # standalone one specifically, not the first (unrelated) hit.
    matches = re.findall(r"(?<!,\s)\.editor-scene::after\s*\{([^}]*)\}", source)
    assert matches, ".editor-scene::after rule not found"
    rule_body = next((body for body in matches if "background" in body), matches[-1])
    assert "linear-gradient(180deg" in rule_body, "the top-to-bottom readability scrim must stay"
    assert "linear-gradient(90deg" not in rule_body, "the left-to-right scrim was supposed to be removed"
    assert "--scene-scrim" not in source, "the horizontal scrim's own custom property should have no readers left"
