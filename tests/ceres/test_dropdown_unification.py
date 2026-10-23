"""Live Chromium coverage for the dropdown-unification batch (2026-08-25).

Reported inconsistency: the main table's filter dropdowns ran through a
shared JS component, while the worker table's filters used the browser's
default `<select>` behavior. The main table's
Rarity/Terrain/Worksite/Type/Sort filters were wrapped by customSelect.js
(app.js's initFilterDropdowns()); the inline candidate picker's own filter
bar (Rarity/Edition/Ability; splinter uses plot icons) emitted raw <select> markup and never
went through the same component -- two dropdown systems in one view. This
file exercises the fix (app.js's initPickerFilterDropdowns()) against a real
board: the picker's selects must be wrapped the SAME way, survive repeated
re-renders (filter change, staging actions) without a blank trigger or a lost
click, and behave identically to the main table's own instance.

Also covers the companion fix in the same batch: the slot editor header no
longer prints the raw internal `deed_uid` -- an internal ID that is not
user-readable, unlike the actual plot number (e.g. 65-10-88) --
`.slot-editor-deed` now shows the real plot number, with the internal id kept
only as a title-attribute tooltip.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_for_board





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


def _open_worker_picker(page: Page) -> int:
    """Expand rows until one with an empty, staffable worker slot is found and
    open its picker. Returns the row index."""
    page.on("pageerror", lambda error: print(f"pageerror: {error}\n{error.stack}"))
    page.on("requestfailed", lambda request: print(f"requestfailed: {request.url} :: {request.failure}"))
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        row = page.locator("#plot-tbody > tr.plot-row").nth(index)
        if row.locator("+ tr.expand-row").evaluate("node => node.style.display") == "none":
            row.click()
        expand = row.locator("+ tr.expand-row")
        expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
        worker = expand.locator(".slot-group[data-group-id='worker']")
        empty = worker.locator("button.slot-tile.is-empty[data-slot-action='add'][data-group-id='worker']")
        if empty.count():
            with page.expect_request("**/api/plots/*/candidates*"):
                empty.first.click()
            page.locator(".inline-picker[data-picker-group='worker']").wait_for(timeout=30_000)
            return index
    pytest.skip("No plot in this account's live snapshot has an empty, staffable worker slot")


def test_picker_filter_selects_are_wrapped_by_custom_select(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    _open_worker_picker(page)
    picker = page.locator(".inline-picker")
    filters = picker.locator(".picker-filters")
    filters.wait_for(timeout=30_000)

    raw_selects = filters.locator("select[data-picker-filter]")
    assert raw_selects.evaluate_all(
        "elements => elements.map(element => element.dataset.pickerFilter)"
    ) == ["rarity", "edition", "ability"]

    # Every raw <select> stays in the DOM (customSelect.js keeps it live for
    # .value/change events) but visually hidden, wrapped by a .csel-wrap with
    # its own styled .csel-trigger -- exactly the main table's own component
    # not a native <select> restyled to merely look similar.
    for i in range(raw_selects.count()):
        select = raw_selects.nth(i)
        assert select.evaluate("el => getComputedStyle(el).opacity") == "0"
        wrap = select.locator("xpath=..")
        assert "csel-wrap" in (wrap.get_attribute("class") or "")
        assert wrap.locator(".csel-trigger").count() == 1

    # Same component the main table's own filters use -- not a lookalike.
    main_trigger_class = page.locator("#rarity-filter").locator("xpath=..").locator(".csel-trigger").get_attribute("class")
    picker_rarity_trigger = filters.locator("select[data-picker-filter='rarity']").locator("xpath=..").locator(".csel-trigger")
    assert picker_rarity_trigger.get_attribute("class") == main_trigger_class
    page.close()


def test_picker_dropdown_open_select_close_and_click_outside(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    _open_worker_picker(page)
    picker = page.locator(".inline-picker")
    rarity_select = picker.locator("select[data-picker-filter='rarity']")
    trigger = rarity_select.locator("xpath=..").locator(".csel-trigger")

    # Open.
    trigger.click()
    list_el = rarity_select.locator("xpath=..").locator(".csel-list")
    assert list_el.is_visible()

    # Select -- a real option click, not a programmatic .value= -- must land
    # (the hazard: handleSlotEditorClick's stopPropagation on [data-slot-action]
    # tiles, or the editor's own outside-click logic, could swallow this).
    epic_option = list_el.locator(".csel-option[data-value='epic']")
    with page.expect_request("**/api/plots/*/candidates*"):
        epic_option.click()
    assert rarity_select.input_value() == "epic"
    assert "epic" in trigger.inner_text().lower()
    # Selecting closes the list.
    assert not list_el.is_visible()

    # Re-open, then click fully outside the picker (a worker tile is inside
    # the same slot editor and calls stopPropagation() -- clicking further
    # out, on the picker's own header, is the real "outside the dropdown but
    # inside the editor" case).
    trigger.click()
    assert list_el.is_visible()
    picker.locator(".picker-bar-title").click()
    assert not list_el.is_visible()
    page.close()


def test_dynamic_edition_list_trigger_is_never_blank(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    _open_worker_picker(page)
    picker = page.locator(".inline-picker")
    edition_select = picker.locator("select[data-picker-filter='edition']")
    trigger = edition_select.locator("xpath=..").locator(".csel-trigger")
    # The known item-34 failure mode: a trigger initialised before its
    # <option>s existed rendered permanently blank. The picker's Edition list
    # is built dynamically per plot -- confirm the trigger actually shows the
    # closed filter label, not an empty trigger. K-4 keeps "All" inside the
    # open list and shows the filter name until a value is selected.
    assert trigger.inner_text().strip() != ""
    assert trigger.inner_text().strip() == "Edition"
    page.close()


def test_picker_survives_rerender_without_stale_or_leaked_dropdown(live_browser: tuple[Browser, str]) -> None:
    """Filter/sort/staging actions rebuild the picker's filter-bar DOM from
    scratch (renderSlotEditor() output). Confirm the NEW <select> is wrapped
    again (not left as a raw unstyled control) and remains interactive."""
    page = _page(live_browser)
    _open_worker_picker(page)
    picker = page.locator(".inline-picker")
    name_input = picker.locator("input[data-picker-filter='q']")
    with page.expect_response("**/api/plots/*/candidates*"):
        name_input.fill("zzz-no-such-card-zzz")
    settle(page)  # the response is drawn after it arrives; read the re-rendered picker, not the old one

    # After the filter round-trip, the rarity select must still be wrapped
    # and still openable -- the fetch path (fetchPickerPage -> updatePickerRegion)
    # only patches .candidate-list/.picker-footer and should NOT have touched
    # the filter bar's own DOM at all, but confirm behaviour end to end.
    rarity_select = picker.locator("select[data-picker-filter='rarity']")
    trigger = rarity_select.locator("xpath=..").locator(".csel-trigger")
    trigger.click()
    list_el = rarity_select.locator("xpath=..").locator(".csel-list")
    assert list_el.is_visible()
    list_el.locator(".csel-option[data-value='']").click()
    assert rarity_select.input_value() == ""
    page.close()


def test_no_raw_deed_uid_in_slot_editor_visible_label(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    row = page.locator("#plot-tbody > tr.plot-row").first
    deed_uid = row.get_attribute("data-deed-uid")
    assert deed_uid
    row.click()
    expand = row.locator("+ tr.expand-row")
    expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)

    visible_text = expand.locator(".slot-editor").inner_text()
    # The internal deed_uid (e.g. "I-294-99ed4b7a4d170e") must never appear in
    # the VISIBLE label -- only a real plot number ("41-9-56" / "65-10-88").
    assert deed_uid not in visible_text
    assert not re.search(r"^[A-Z]-\d+-[0-9a-f]+$", visible_text.strip())
    # The editor no longer prints a "Plot 41-9-56" caption of its own (the plot row right above
    # it names the plot); where the editor does name the plot -- its close control -- it uses
    # the same plot number as that row, never the deed uid.
    plot_label = row.locator("td").first.inner_text().strip()
    assert re.fullmatch(r"\d{1,3}-\d{1,2}-\d{1,3}", plot_label), plot_label
    close_label = expand.locator(".eos-close").first.get_attribute("aria-label") or ""
    assert close_label == f"Close plot {plot_label}", close_label
    labels = expand.locator(".slot-editor [aria-label]").evaluate_all("els => els.map((el) => el.getAttribute('aria-label'))")
    assert not [label for label in labels if deed_uid in label]
    page.close()
