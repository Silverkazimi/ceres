"""Live Chromium coverage for CARD-level BLOODLINE marking (v0.5.20 follow-up).

Reported on v0.5.20: the new solo/paired bloodline badge existed, but the
cards themselves were still not marked as a bloodline pair or not -- a card
could be removed without warning, and hovering over one showed no tooltip
info about the bloodline at all. That
badge/toast/tooltip trio existed technically (bloodlineCardBadgeHtml(),
editorWorkerTip(), the removal-impact toast) but was too subtle to read as
"marked" -- this file exercises the real thing against real cached-snapshot
plots, the way test_live_slot_editor.py does, not a fixture stand-in.

Known real cases (the internal rules / the dispatching task): `I-294-99ed4b7a4d170e`
(Kinbound Druid, Elf bloodline, companion Prince Rennyn -- ACTIVE) and
`I-293-fd92f682618429` (Phlegmwrought, Divine Construct -- SOLO, no companion).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_for_board
from . import board_basic_recording as recording


CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"
CANONICAL = Path(__file__).parents[2] / "ceres" / "static" / "canonical.css"
SCREEN_DIR = Path(__file__).parents[2] / "work" / "ceres-fr1698-items"

PAIRED_DEED = recording.OVERCAP.uid
SOLO_DEED = recording.SOLO.uid

# Mirrors tests/ceres/test_power_warning_contrast.py's own fixture -- the four
# Iris themes this board actually ships, read directly from canonical.css.
THEME_TOKENS = {
    "alchemical": ("#c9a84c", "#1a1714"),
    "dark": ("#CABEE4", "#231f30"),
    "light": ("#453C68", "#ccc7d8"),
    "wood": ("#3A2410", "#cfc0a0"),
}
WCAG_AA_MIN = 4.5


def _srgb_channel(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    r, g, b = _srgb_channel(r), _srgb_channel(g), _srgb_channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1, l2 = sorted((_relative_luminance(fg_hex), _relative_luminance(bg_hex)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_slot_bloodline_badge_uses_bg3_heading_pairing() -> None:
    # Same proven >=4.5:1-in-every-theme combo `.plot-warning-row` and
    # `.slot-hazard-inline` already rely on (the internal rules) -- a bare text colour
    # over a photographic card-art background has no guaranteed contrast, so
    # the badge is a solid pill instead.
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.slot-bloodline-badge\s*\{([^}]*)\}", source)
    assert match is not None, ".slot-bloodline-badge rule not found"
    rule_body = match.group(1)
    assert "background: var(--bg3)" in rule_body
    assert "color: var(--heading)" in rule_body


def test_plot_warning_row_uses_bg3_heading_pairing() -> None:
    # the pair-break warning is now a row of the plot warning list.
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\n\.plot-warning-row\s*\{([^}]*)\}", source)
    assert match is not None, ".plot-warning-row rule not found"
    rule_body = match.group(1)
    assert "background: var(--bg3)" in rule_body
    assert "color: var(--heading)" in rule_body


def test_bloodline_badge_and_warning_contrast_meets_wcag_aa_in_every_theme() -> None:
    for theme, (heading_hex, bg3_hex) in THEME_TOKENS.items():
        ratio = _contrast_ratio(heading_hex, bg3_hex)
        assert ratio >= WCAG_AA_MIN, f"{theme}: {ratio:.2f}:1 below WCAG AA {WCAG_AA_MIN}:1"



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
    # The editor band mounts while the plot's detail is still being read ("preparing"); the
    # cards are drawn once it is ready -- the open now reads the plot through POST .../expand
    # (the page load's capture drops earlier per-plot reads), which takes a moment longer.
    expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    return expand


def test_paired_bloodline_card_shows_labelled_badge_and_full_tooltip(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    expand = _expand_deed(page, PAIRED_DEED)
    druid_tile = expand.locator(".slot-tile").filter(has_text="Kinbound Druid").first
    # the pre- caption badge ("PAIRED"/"SOLO"/"COMPANION", `.slot-bloodline-badge`) is retired
    # the tile now carries Echo's labelled pill at its bottom (`.slot-kin-badge`, badges.js bloodlineTileBadgesHtml).
    badge = druid_tile.locator(".slot-tags .slot-kin-badge")  # the top-left tag row
    assert badge.count() == 1
    assert (badge.get_attribute("aria-label") or "").startswith("Toil & Kin +")  # the full wording is the accessible name
    assert badge.inner_text().strip().startswith("+")  # ... the tile shows the icon + "+N%"
    assert "is-paired" in (badge.get_attribute("class") or "")
    # tipAttrs() (app.js) deliberately never emits a native `title` -- Ceres
    # has its own styled hover/focus tooltip driven by `data-tip`/`aria-label`
    # (batch 9 item 11: a native title renders below-right, hidden behind the
    # cursor). Read `data-tip` directly rather than hovering + reading
    # #ceres-tooltip -- equivalent content, no hover-timing flakiness.
    tip = badge.get_attribute("data-tip") or ""
    body = badge.get_attribute("data-tip-body") or ""
    assert tip == "Toil and Kin · Elf"
    assert "Elf companion is staked here" in body
    page.close()


def test_land_ability_opens_on_click_and_identifies_plot_wide_scope(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    expand = _expand_deed(page, PAIRED_DEED)
    icon = expand.locator("[data-ability-tip][data-tip='Toil and Kin']").first
    assert icon.count() == 1
    body = icon.get_attribute("data-tip-body") or ""
    assert "Affects every worker on this plot." in body
    icon.hover()
    assert page.locator("#ceres-tooltip").is_hidden()
    icon.click()
    page.locator("#ceres-tooltip").wait_for(state="visible", timeout=5_000)
    assert "Affects every worker on this plot." in page.locator("#ceres-tooltip").inner_text()
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / "item-27-ability-click-plot-wide.png"), full_page=True)
    page.close()


def test_solo_bloodline_card_shows_solo_badge_naming_the_missing_bloodline(live_browser: tuple[Browser, str]) -> None:
    # RULING CHANGE (2026-08-26, supersedes the earlier
    # fact-not-defect wording ruling): SOLO is now a warning badge, and its
    # tooltip must say a companion is MISSING and name the specific Bloodline
    # never the internal `sub_type` field name.
    page = _page(live_browser)
    expand = _expand_deed(page, SOLO_DEED)
    solo_tile = expand.locator(".slot-tile").filter(has_text="Phlegmwrought").first
    badge = solo_tile.locator(".slot-tags .slot-kin-badge")  # the top-left tag row ( pill)
    assert badge.count() == 1
    assert badge.get_attribute("aria-label") == "Toil & Kin: Solo"  # the full wording is the accessible name
    assert badge.inner_text().strip() == "Solo"  # ... the tile shows the icon + "Solo"
    assert "is-solo" in (badge.get_attribute("class") or "")
    tip = badge.get_attribute("data-tip") or ""
    body = (badge.get_attribute("data-tip-body") or "").lower()
    assert tip == "Toil and Kin · Divine Construct"
    assert "no divine construct companion is staked" in body  # names the missing Bloodline
    assert "sub_type" not in body
    page.close()


def test_removing_active_companion_stages_and_warns_by_name_non_blocking(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    expand = _expand_deed(page, PAIRED_DEED)
    companion_tile = expand.locator(".slot-tile").filter(has_text="Prince Rennyn").first
    companion_tile.hover()
    companion_tile.locator(".slot-remove").click()
    warning = expand.locator('.plot-warning-row[data-plot-warning-kind="bloodline"]')
    warning.wait_for(timeout=15_000)
    warning_text = warning.inner_text()
    assert "Toil and Kin" in warning_text
    assert "Elf" in warning_text
    assert "every worker" in warning_text

    # Non-blocking (ruling: warn, never block) -- the removal
    # actually landed. The slot no longer renders as a plain empty "Add
    # Worker" tile, though (superseded 2026-08-25 by the "click an empty tile
    # to bring back a removed card" feature, tests/ceres/test_live_restore_tile.py)
    # it ghosts Prince Rennyn back in with a "restore" affordance so the undo
    # is discoverable, per the in-game-parity request. Review/Sign/Verify
    # is not disabled BECAUSE of this warning (it may still be disabled for the
    # unrelated "no Keychain" reason, which is fine -- staging itself must not
    # be gated).
    ghost_tile = expand.locator(".slot-tile.is-pending-removal").filter(has_text="Prince Rennyn")
    assert ghost_tile.count() == 1
    assert ghost_tile.get_attribute("data-slot-action") == "restore"
    # "Disabled" here (if it happens) must be for the pre-existing, unrelated
    # "no Keychain detected" reason -- never because a bloodline warning
    # exists. Either the button is enabled, or its own tip names Keychain.
    # The per-plot Review button is retired: every staged change is reviewed from the one
    # Changes button (#checkout-btn, checkout.js), which must show and stay usable.
    changes = page.locator("#checkout-btn")
    changes.wait_for(state="visible", timeout=15_000)
    assert changes.is_enabled(), "the pair-break warning must never gate opening Changes"

    # The Kinbound Druid tile (the surviving half) re-renders live as SOLO.
    druid_badge = expand.locator(".slot-tile").filter(has_text="Kinbound Druid").first.locator(".slot-kin-badge")
    assert druid_badge.get_attribute("aria-label") == "Toil & Kin: Solo"  # the full wording is the accessible name
    assert druid_badge.inner_text().strip() == "Solo"  # ... the tile shows the icon + "Solo"
    page.close()


def test_removing_solo_holder_never_triggers_a_bloodline_warning(live_browser: tuple[Browser, str]) -> None:
    # A solo BLOODLINE holder was never active (no companion), so removing it
    # can't break a pair -- the pair-break warning must never fire for it
    # even though SOLO is itself now a warning badge.
    page = _page(live_browser)
    expand = _expand_deed(page, SOLO_DEED)
    solo_tile = expand.locator(".slot-tile").filter(has_text="Phlegmwrought").first
    solo_tile.hover()
    remove_btn = solo_tile.locator(".slot-remove")
    if remove_btn.count() == 0:
        pytest.skip("Phlegmwrought tile is not actionable in this live snapshot")
    remove_btn.click()
    page.wait_for_function(
        "() => !document.querySelector('tr.expand-row .slot-tile')?.textContent?.includes('loading')",
    )
    settle(page)
    assert expand.locator('.plot-warning-row[data-plot-warning-kind="bloodline"]').count() == 0
    page.close()
