"""Rendered tooltip and combine-dialog accessibility coverage."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Callable, Iterator

from playwright.sync_api import Page

from .conftest import LIVE_PLAYER, spawn_live_ceres
from .idle_pool import serve_recorded_idle_pool
from . import board_basic_recording as recording


@contextmanager
def _live_page(before_load: Callable[[Page, str], object] | None = None) -> Iterator[Page]:
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            if before_load:
                before_load(page, base_url)
            page.goto(f"{base_url}/?player={LIVE_PLAYER}", wait_until="domcontentloaded")
            page.wait_for_selector("#plot-tbody > tr.plot-row", timeout=30_000)
            yield page
        finally:
            page.close()


def _expand_until_ability_tooltip(page: Page) -> int:
    # Recorded Baxdon has both Toil and Kin and a worker-only ability. Do not
    # scan an maintainer's changing board or silently skip absent fixture coverage.
    page.locator(f"tr.plot-row[data-deed-uid='{recording.ABILITIES.uid}']").click()
    page.wait_for_selector("[data-ability-tip]", timeout=10_000)
    return page.locator("[data-ability-tip]").count()


def _open_combine_dialog(page: Page) -> None:
    # The recorded Daria worker has three synthetic idle partners in the
    # browser-combine overlay. Use the current per-tile entry point.
    deed_uid = recording.COMBINE.uid
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(f".tile-combine-chip[data-deed-uid='{deed_uid}']").first.click()
    page.wait_for_function(
        "document.querySelector('#combine-dialog-overlay')?.hidden === false",
        timeout=10_000,
    )


# INJECTION: joining the ability name and effect into one tooltip string.
def test_ability_tooltips_split_name_and_body() -> None:
    with _live_page() as page:
        count = _expand_until_ability_tooltip(page)
        assert count, "No rendered ability tooltip was found after expanding the plot rows."
        for index in range(count):
            tip = page.locator("[data-ability-tip]").nth(index)
            body = tip.get_attribute("data-tip-body") or ""
            title = tip.get_attribute("data-tip") or ""
            assert body.strip(), f"Ability tooltip {index} has an empty data-tip-body."
            assert body not in title, f"Ability tooltip {index} still joins its body into data-tip."


# INJECTION: omitting or adding the plot-wide scope sentence incorrectly.
def test_plot_wide_tooltip_body_has_scope_sentence() -> None:
    with _live_page() as page:
        count = _expand_until_ability_tooltip(page)
        assert count, "No rendered ability tooltip was found after expanding the plot rows."
        tips = [page.locator("[data-ability-tip]").nth(index) for index in range(count)]
        plot_wide = [tip for tip in tips if tip.get_attribute("data-tip") == "Toil and Kin"]
        assert plot_wide, "The fixture must render a plot-wide ability."
        for tip in plot_wide:
            body = tip.get_attribute("data-tip-body") or ""
            assert body.endswith("Affects every worker on this plot.")
        non_plot_wide = [tip for tip in tips if tip.get_attribute("data-tip") != "Toil and Kin"]
        assert non_plot_wide, "No non-plot-wide ability rendered alongside the plot-wide ability."
        for tip in non_plot_wide:
            body = tip.get_attribute("data-tip-body") or ""
            assert not body.endswith("Affects every worker on this plot.")


# INJECTION: leaking the tool name or API vocabulary into player-facing labels.
def test_rendered_labels_are_player_facing() -> None:
    with _live_page() as page:
        _expand_until_ability_tooltip(page)
        # The header logo link (added with the start page) is the product's own brand mark:
        # its visible content is the "Ceres" logo, so its accessible name must contain the
        # word (WCAG 2.5.3, label in name). Every other label stays free of the tool name.
        logo = page.locator("a.board-logo-link")
        assert logo.get_attribute("data-tip") == "Start page"
        assert logo.locator("img").get_attribute("alt") == "Ceres"
        fields = page.locator("[data-tip], [data-tip-body], [data-tip-foot], [aria-label]")
        for index in range(fields.count()):
            element = fields.nth(index)
            is_logo = element.evaluate("el => el.matches('a.board-logo-link')")
            for attribute in ("data-tip", "data-tip-body", "data-tip-foot", "aria-label"):
                value = element.get_attribute(attribute) or ""
                if not (is_logo and attribute == "aria-label"):
                    assert "Ceres" not in value, f"{attribute} exposes the tool name: {value!r}"
                assert not re.search(r"\bserver\b", value, re.IGNORECASE), f"{attribute} exposes server vocabulary: {value!r}"


# INJECTION: removing or changing the combine copy-list accessible name.
def test_combine_copy_list_has_accessible_name() -> None:
    # The tile chip reads the board's idle-card pool, which a page load on the shared fixture
    # server never reports as loaded (tests/ceres/idle_pool.py); serve the recorded account's
    # real pool, read after every recorded plot was read, so the Daria partners are there.
    with _live_page(serve_recorded_idle_pool) as page:
        _open_combine_dialog(page)
        copy_list = page.locator("#combine-dialog-overlay [aria-label='Cards in this combine']")
        assert copy_list.count(), "The combine dialog copy list lacks its required accessible name."
