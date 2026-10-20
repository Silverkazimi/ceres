from __future__ import annotations

import re

import pytest

from tests.ceres.conftest import spawn_live_ceres, wait_for_board


def _loaded_page():
    context = spawn_live_ceres()
    browser, base_url = context.__enter__()
    page = browser.new_page()
    page.goto(base_url)
    wait_for_board(page)
    return context, page


def _column_cells(page, heading):
    header = page.locator("th").filter(has_text=re.compile(rf"^{re.escape(heading)}$"))
    assert header.count() == 1
    column = header.evaluate("element => element.cellIndex + 1")
    table = page.locator("table.plot-table").filter(has=page.locator("tbody tr"))
    assert table.count() == 1
    return table.locator(f"tbody tr td:nth-child({column})")


def test_lane_icons_use_explain_tooltips():
    """Revert splinterIconHtml to a bare string -> red naming .splinter-icon."""
    context, page = _loaded_page()
    try:
        attributes = page.locator(
            "img.rarity-gem, img.terrain-emblem, img.resource-icon, "
            "img.type-mark, img.splinter-icon, img.set-icon"
        ).evaluate_all(
            """elements => elements.map(element => ({
                hasTip: element.hasAttribute('data-tip'),
                tip: element.getAttribute('data-tip'),
                kind: element.getAttribute('data-tip-kind'),
                body: element.getAttribute('data-tip-body'),
                key: element.getAttribute('data-tip-key'),
            }))"""
        )
        tipped = [item for item in attributes if item["hasTip"]]
        assert attributes
        assert all(item["hasTip"] for item in attributes)
        label_tipped = [item for item in tipped if item["body"] is None]
        resource_tipped = [item for item in tipped if item["body"] is not None]
        # peer-group depth owns presentation; a title-only tooltip is
        # still an explain contract, not a separate label style.
        assert all(item["kind"] == "explain" for item in label_tipped)
        assert all(item["body"] is None for item in label_tipped)
        assert all(item["key"] is None for item in label_tipped)
        assert all(item["kind"] == "explain" for item in resource_tipped)

    finally:
        page.close()
        context.__exit__(None, None, None)


def test_rarity_text_has_no_tooltip():
    """Re-add tipAttrs to the rarity-text fallback span -> red."""
    context, page = _loaded_page()
    try:
        assert page.locator("span.rarity-text").evaluate_all(
            "elements => elements.every(element => !element.hasAttribute('data-tip'))"
        )
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_set_label_loses_tooltip_but_icon_keeps_it():
    """Re-add tipAttrs to the set-name-label span -> red."""
    context, page = _loaded_page()
    try:
        labels = page.locator("span.set-name-label")
        icons = page.locator("img.set-icon:has(+ span.set-name-label)")
        assert labels.evaluate_all(
            "elements => elements.every(element => !element.hasAttribute('data-tip'))"
        )
        assert icons.evaluate_all(
            "elements => elements.every(element => element.hasAttribute('data-tip'))"
        )
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_lane_icon_tooltips_do_not_say_magical():
    """Restore the Magical prefix on the element tooltip -> red."""
    context, page = _loaded_page()
    try:
        tips = page.locator("img.type-mark-element").evaluate_all(
            """elements => elements
            .map(element => element.getAttribute('data-tip'))
            .filter(tip => tip !== null)"""
        )
        assert tips
        assert all("Magical" not in tip for tip in tips)
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_lane_icon_aria_labels_match_tooltips():
    """Remove the generated aria-label from a queue icon -> red."""
    context, page = _loaded_page()
    try:
        attributes = page.locator(
            "img.rarity-gem, img.terrain-emblem, img.resource-icon, "
            "img.type-mark, img.splinter-icon, img.set-icon"
        ).evaluate_all(
            """elements => elements
            .filter(element => element.hasAttribute('data-tip'))
            .map(element => ({
                tip: element.getAttribute('data-tip'),
                aria: element.getAttribute('aria-label'),
                body: element.getAttribute('data-tip-body'),
            }))"""
        )
        assert attributes
        resource_attributes = [item for item in attributes if item["body"] is not None]
        label_attributes = [item for item in attributes if item["body"] is None]
        assert all(item["aria"] == item["tip"] for item in label_attributes)
        assert all(item["aria"].startswith(item["tip"]) for item in resource_attributes)
        assert all(item["body"] in item["aria"] for item in resource_attributes)
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_plot_tooltip_includes_region_and_deed_uid():
    """Revert territoryCellHtml to tipAttrs({ title: territory }) -> red."""
    context, page = _loaded_page()
    try:
        attributes = page.locator("td.plot-cell[data-tip]").evaluate_all(
            """elements => elements.map(element => ({
                tip: element.getAttribute('data-tip'),
                body: element.getAttribute('data-tip-body'),
                aria: element.getAttribute('aria-label'),
            }))"""
        )
        assert attributes
        for item in attributes:
            assert re.fullmatch(r"[A-Za-z' -]+", item["tip"])
            assert re.fullmatch(r"I-\d+-[0-9a-f]+", item["body"])
            assert item["tip"] in item["aria"]
            assert item["body"] in item["aria"]
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_harvest_per_hour_resources_keep_icons_without_worksite_tooltip_input():
    """Restore ``|| !worksiteType`` in resourceIconHtml's guard -> red."""
    context, page = _loaded_page()
    try:
        cells = _column_cells(page, "Harvest/hr")
        values = cells.evaluate_all(
            """elements => elements
            .filter(element => element.textContent.trim() && element.textContent.trim() !== '—')
            .map(element => ({
                value: element.textContent.trim(),
                icons: element.querySelectorAll('img.resource-icon').length,
            }))"""
        )
        assert values
        assert all(item["icons"] >= 1 for item in values)
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_worksite_modal_needs_resources_keep_icons_without_worksite_tooltip_input():
    """Restore ``|| !worksiteType`` in resourceIconHtml's guard -> red."""
    context, page = _loaded_page()
    try:
        worksite_cells = _column_cells(page, "Worksite").filter(has=page.locator("img"))
        assert worksite_cells.count()
        worksite_cells.first.click()
        modal_button = page.locator("tr.expand-row button.worksite-change-btn").first
        modal_button.wait_for(timeout=10_000)
        modal_button.click()
        page.locator("#worksite-modal-overlay").wait_for(state="visible", timeout=10_000)
        needs = page.locator(".worksite-tile-needs-icons:visible")
        assert needs.count()
        assert needs.locator("img").count() >= 1
    finally:
        page.close()
        context.__exit__(None, None, None)


def test_worksite_column_keeps_icons_and_genuine_missing_worksites_as_dashes():
    """Restore ``|| !worksiteType`` in resourceIconHtml's guard -> red."""
    context, page = _loaded_page()
    try:
        cells = _column_cells(page, "Worksite")
        values = cells.evaluate_all(
            """elements => elements.map(element => ({
                text: element.textContent.trim(),
                icons: element.querySelectorAll('img').length,
            }))"""
        )
        assert values
        missing = [item for item in values if item["text"] == "—"]
        with_icons = [item for item in values if item["text"] != "—"]
        if not missing:
            pytest.skip("No genuinely missing worksite is present in the live snapshot")
        if not with_icons:
            pytest.skip("No populated worksite is present in the live snapshot")
        assert all(item["icons"] == 0 for item in missing)
        assert all(item["icons"] > 0 for item in with_icons)
    finally:
        page.close()
        context.__exit__(None, None, None)
