"""Live Chromium coverage for two related v0.5.23 follow-ups (2026-08-25).

JOB 1 -- clicking on the tile instead of the badge was found to remove the
card's image with nothing able to bring it back afterwards, leaving it black.
Root-caused in `work/ceres-tileclick-bug/findings.md`:
`mutateEditorSlot()` dropped every card-identity field art depends on
(`editions`, `edition`, `card_set`, `card_name`, `foil`, `gold`, ...), so the
next render defaulted to edition 0 -> the wrong CDN set -> a 404 whose
`onerror` chain strips the `<img>` permanently. The regression that matters is
a NON-`cards_v2.2` card (edition 0 happens to be correct for `cards_v2.2`
cards, which is why "the badge works" was a card-dependent illusion) -- this
uses Prince Rennyn (`cards_beta`) on `I-294-99ed4b7a4d170e`, per the dispatch
brief.

JOB 2 -- the land card showed a "Paired" badge while the paired card had no
badge at all and nothing in its tooltip -- both sides of a pairing should show
the badge. Companion badge/tooltip added via
`bloodlineCompanionBadgeHtml()`/`bloodlineCompanionRelations()` in app.js,
reusing the SAME `bloodlineAbilityTip()` decoder the holder's badge already
uses (with `role: 'companion'`) so the two sides can't disagree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres, wait_for_board
from . import board_basic_recording as recording


SCREEN_DIR = Path(__file__).parents[2] / "work" / "ceres-tilefix"
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

PAIRED_DEED = recording.OVERCAP.uid



@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    return page


def _expand_deed(page: Page, deed_uid: str):
    row = page.locator(f'tr.plot-row[data-deed-uid="{deed_uid}"]')
    if row.count() == 0:
        pytest.skip(f"{deed_uid} not present in this account's live snapshot")
    if row.locator("+ tr.expand-row").evaluate("node => node.style.display") == "none":
        row.click()
    expand = row.locator("+ tr.expand-row")
    expand.locator(".slot-editor").wait_for(timeout=30_000)
    return expand


@pytest.mark.parametrize(
    "click_target",
    ["tile-background", "checkout-badge"],
)
def test_restore_survives_with_art_for_non_v22_card(live_browser: tuple[Browser, str], click_target: str) -> None:
    page = _page(live_browser)
    expand = _expand_deed(page, PAIRED_DEED)

    rennyn_tile = expand.locator(".slot-tile").filter(has_text="Prince Rennyn").first
    assert rennyn_tile.count() == 1, "Prince Rennyn not staked on this plot in the live snapshot"
    # Confirm it starts with real art (a `cards_beta` card -- the regression
    # that matters; a `cards_v2.2` card would pass even with the old bug).
    assert rennyn_tile.locator("img").count() == 1
    art_src_before = rennyn_tile.locator("img").get_attribute("src") or ""
    assert "cards_beta" in art_src_before

    rennyn_tile.hover()
    rennyn_tile.locator(".slot-remove").click()

    ghost = expand.locator(".slot-tile.is-pending-removal").filter(has_text="Prince Rennyn")
    ghost.wait_for(timeout=15_000)
    page.screenshot(path=str(SCREEN_DIR / f"01_{click_target}_ghost.png"))

    if click_target == "tile-background":
        ghost.click(position={"x": 5, "y": 5})
    else:
        # checkout-dock badge -- the removal is staged, so the checkout dock's
        # own remove-action control for this asset is the remaining click target.
        # The button itself is a bare "x" (data-checkout-action='remove')
        # the card name lives in its sibling `.checkout-move-copy`, so filter
        # on the containing `<li class="checkout-move">` instead.
        page.locator("#checkout-btn").click()
        dock_item = page.locator("li.checkout-move").filter(has_text="Prince Rennyn").first
        if dock_item.count() == 0:
            pytest.skip("checkout dock entry not found for Prince Rennyn in this snapshot layout")
        dock_item.locator("[data-checkout-action='remove']").click()

    restored_tile = expand.locator(".slot-tile").filter(has_text="Prince Rennyn").first
    restored_tile.wait_for(timeout=15_000)
    assert "is-pending-removal" not in (restored_tile.get_attribute("class") or "")

    # THE regression: art must survive, not go black/stripped.
    art = restored_tile.locator("img")
    assert art.count() == 1, f"restore via {click_target} stripped the <img> (black tile)"
    art_src_after = art.get_attribute("src") or ""
    assert "cards_beta" in art_src_after, f"restore via {click_target} resolved the wrong CDN set: {art_src_after!r}"
    assert "cards_v2.2" not in art_src_after
    page.screenshot(path=str(SCREEN_DIR / f"02_{click_target}_restored.png"))

    # Secondary defects the same fix resolves: land_abilities/sole_holder_for
    # survive (tooltip no longer reads "Unknown"), and the tile's title/level
    # are correctly identified rather than defaulting.
    tip = restored_tile.get_attribute("data-tip") or ""
    assert "Unknown" not in tip
    assert "Prince Rennyn" in restored_tile.inner_text()
    page.close()


def test_holder_and_companion_both_carry_bloodline_badge_and_tooltip(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    expand = _expand_deed(page, PAIRED_DEED)

    druid_tile = expand.locator(".slot-tile").filter(has_text="Kinbound Druid").first
    # the old caption badges ("PAIRED"/"COMPANION", `.slot-bloodline-badge`) are retired; both tiles
    # now carry Echo's labelled pill in the tile's bottom `.slot-foot` (badges.js bloodlineTileBadgesHtml).
    druid_badge = druid_tile.locator(".slot-tags .slot-kin-badge")  # the top-left tag row
    assert druid_badge.count() == 1
    assert (druid_badge.get_attribute("aria-label") or "").startswith("Toil & Kin +")  # the full wording is the accessible name
    assert druid_badge.inner_text().strip().startswith("+")  # ... the tile shows the icon + "+N%"
    assert "is-paired" in (druid_badge.get_attribute("class") or "")
    assert druid_badge.get_attribute("data-tip") == "Toil and Kin · Elf"
    holder_tip = druid_badge.get_attribute("data-tip-body") or ""
    assert "Elf companion is staked here" in holder_tip

    rennyn_tile = expand.locator(".slot-tile").filter(has_text="Prince Rennyn").first
    companion_badge = rennyn_tile.locator(".slot-tags .slot-kin-badge")  # the top-left tag row
    assert companion_badge.count() == 1, "companion tile must carry its own badge, not render nothing"
    assert companion_badge.inner_text().strip() == "Companion"
    assert "is-companion" in (companion_badge.get_attribute("class") or "")
    assert companion_badge.get_attribute("data-tip") == "Companion · Elf"
    companion_tip = companion_badge.get_attribute("data-tip-body") or ""
    assert "Kinbound Druid" in companion_tip
    # Wording distinguishes roles: the companion COMPLETES the holder's pair, never carries the ability.
    assert "completes" in companion_tip.lower()

    # The tile-wide tooltip (not just the badge) also names the relationship
    # from the companion's own side.
    full_tip = rennyn_tile.get_attribute("data-tip-body") or ""
    assert "Kinbound Druid" in full_tip
    page.close()
