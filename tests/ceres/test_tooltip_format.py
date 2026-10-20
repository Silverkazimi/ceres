"""Rendered tooltip contracts for the Ceres board."""

from .conftest import spawn_live_ceres, wait_for_board


def _loaded_page(browser, base_url):
    page = browser.new_page()
    page.goto(base_url)
    wait_for_board(page)
    return page


def test_totem_tooltips_split_name_and_boost():
    """Bundled string passed back to totemIconHtml -> red naming img.totem-icon."""
    with spawn_live_ceres() as (browser, base_url):
        page = _loaded_page(browser, base_url)
        for element in page.locator("img.totem-icon[data-tip]").all():
            title = element.get_attribute("data-tip")
            body = element.get_attribute("data-tip-body")
            assert body
            assert "Boosts" not in title
            assert "Boosts" in body


def test_title_tooltips_split_name_and_boost():
    """Bundled string passed back to titleIconHtml -> red naming img.title-icon."""
    with spawn_live_ceres() as (browser, base_url):
        page = _loaded_page(browser, base_url)
        for element in page.locator("img.title-icon[data-tip]").all():
            title = element.get_attribute("data-tip")
            body = element.get_attribute("data-tip-body")
            assert body
            assert "Boosts" not in title
            assert "Boosts" in body


def test_multiplier_breakdown_has_no_tooltip():
    """Multiplier breakdown tooltip retained -> red on div.multiplier-breakdown."""
    with spawn_live_ceres() as (browser, base_url):
        page = _loaded_page(browser, base_url)
        for element in page.locator("div.multiplier-breakdown").all():
            assert element.get_attribute("data-tip") is None


def test_dec_efficiency_flags_are_name_tooltips():
    """: title-only peer content uses the explain contract."""
    with spawn_live_ceres() as (browser, base_url):
        page = _loaded_page(browser, base_url)
        for element in page.locator("span.dec-efficiency-flag").all():
            assert element.get_attribute("data-tip-kind") == "explain"
            assert element.get_attribute("data-tip-body") is None


def test_icon_aria_labels_preserve_split_tooltip_text():
    """Accessible name built from the bundled tooltip -> red on split icon aria-label."""
    with spawn_live_ceres() as (browser, base_url):
        page = _loaded_page(browser, base_url)
        for selector in ("img.totem-icon[data-tip]", "img.title-icon[data-tip]"):
            for element in page.locator(selector).all():
                title = element.get_attribute("data-tip")
                body = element.get_attribute("data-tip-body")
                assert element.get_attribute("aria-label") == f"{title}. {body}"
