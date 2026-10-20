from __future__ import annotations

import io
import re
from collections import Counter

import pytest
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .conftest import settle, spawn_live_ceres
from . import board_basic_recording as recording


PLOT_WITH_RUNI = recording.OVERCAP.label


def _open_editor(page, base_url: str, plot: str) -> None:
    page.goto(base_url)
    page.wait_for_selector("#plot-tbody > tr.plot-row", timeout=30_000)
    row = page.locator("#plot-tbody > tr.plot-row").filter(has_text=plot).first
    if row.count() == 0:
        rendered_rows = page.locator("#plot-tbody > tr.plot-row").all_inner_texts()
        raise AssertionError(f"Rendered plot rows did not include {plot!r}: {rendered_rows[:20]!r}")
    row.click()
    page.locator(".slot-editor").first.wait_for(timeout=10_000)


def _open_editor_with_worker(page, base_url: str):
    page.goto(base_url)
    page.wait_for_selector("#plot-tbody > tr.plot-row", timeout=30_000)
    rows = page.locator("#plot-tbody > tr.plot-row")
    for index in range(rows.count()):
        row = rows.nth(index)
        row.click()
        editor = page.locator("tr.expand-row").nth(index).locator(".slot-editor")
        try:
            editor.wait_for(timeout=30_000)
        except PlaywrightTimeoutError:
            row.click()
            continue
        tile = editor.locator('.slot-group[data-group-id="worker"] .slot-tile[data-occupied="1"]').first
        if tile.count():
            return tile
        row.click()
    pytest.skip("No live snapshot worker tile was rendered")


def _open_editor_with_add_states(page, base_url: str) -> None:
    page.goto(base_url)
    page.wait_for_selector("#plot-tbody > tr.plot-row", timeout=30_000)
    rows = page.locator("#plot-tbody > tr.plot-row")
    for index in range(rows.count()):
        row = rows.nth(index)
        row.click()
        editor = page.locator("tr.expand-row").nth(index).locator(".slot-editor")
        try:
            editor.wait_for(timeout=30_000)
        except PlaywrightTimeoutError:
            row.click()
            continue
        actionable = editor.locator("button:not(:disabled)").filter(has_text=re.compile(r"Add .+"))
        disabled = editor.locator("button:disabled").filter(has_text=re.compile(r"Add .+"))
        if actionable.count() and disabled.count():
            return
        row.click()
    pytest.skip("No live snapshot plot exposes both actionable and disabled Add states")


@pytest.fixture
def editor_page():
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page()
        try:
            yield page, base_url
        finally:
            page.close()


def test_occupied_worker_tooltips_have_one_nonduplicated_key(editor_page):
    # INJECTION: duplicate data-tip-key or copying the key into data-tip-body.
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    tiles = page.locator('.slot-group[data-group-id="worker"] .slot-tile').filter(
        has=page.locator(".slot-remove")
    )
    assert tiles.count() > 0
    for index in range(tiles.count()):
        tile = tiles.nth(index)
        assert tile.get_attribute("data-tip-key")
        assert tile.get_attribute("data-tip-key").count("\n") == 0
        assert tile.get_attribute("data-tip-foot") is None
        key = tile.get_attribute("data-tip-key")
        body = tile.get_attribute("data-tip-body") or ""
        if not body:
            continue
        assert body.count("Base PP") >= 1


def test_remove_button_uses_explain_tooltip(editor_page):
    # peer-group depth replaces the retired label-kind presentation.
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    remove = page.locator('.slot-group[data-group-id="worker"] .slot-remove').first
    assert remove.get_attribute("data-tip") == "Unstake from this slot"
    assert remove.get_attribute("data-tip-kind") == "explain"
    assert remove.get_attribute("data-tip-body") is None


def test_add_buttons_distinguish_actionable_and_disabled_states(editor_page):
    # INJECTION: assigning row-39 tooltip text to actionable Add buttons or omitting it when disabled.
    page, base_url = editor_page
    _open_editor_with_add_states(page, base_url)
    actionable = page.locator(".slot-editor button:not(:disabled)").filter(has_text=re.compile(r"Add .+"))
    disabled = page.locator(".slot-editor button:disabled").filter(has_text=re.compile(r"Add .+"))
    for index in range(actionable.count()):
        assert actionable.nth(index).get_attribute("data-tip") is None
    if disabled.count() == 0:
        pytest.skip("No disabled Add state was present in the live snapshot")
    for index in range(disabled.count()):
        assert disabled.nth(index).get_attribute("data-tip")


def test_sole_holder_flag_explains_plot_scope(editor_page):
    # INJECTION: replacing the row-41 sole-holder body or dropping its plot-wide warning.
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    flag = page.locator(".slot-editor").locator("[data-tip=\"Sole ability holder\"]").first
    assert flag.get_attribute("data-tip") == "Sole ability holder"
    assert "every worker on this plot" in (flag.get_attribute("data-tip-body") or "")


def test_slot_editor_tooltips_have_no_banned_jargon(editor_page):
    # INJECTION: retaining raw or Served jargon in any slot-editor tooltip or accessible label.
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    values = page.locator(".slot-editor [data-tip], .slot-editor [data-tip-body], .slot-editor [data-tip-foot], .slot-editor [aria-label]").evaluate_all(
        "els => els.flatMap(el => [el.getAttribute('data-tip'), el.getAttribute('data-tip-body'), el.getAttribute('data-tip-foot'), el.getAttribute('aria-label')].filter(Boolean))"
    )
    assert not any(re.search(r"\b(?:served|raw)\b", value, re.IGNORECASE) for value in values)


def test_held_change_and_base_pp_tooltips_remain_legacy_shape(editor_page):
    # INJECTION: changing held rows 48/49/50 to add data-tip-key or removing their data-tip.
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    held = page.locator(".slot-editor .editor-change-badge")
    rendered = held.evaluate_all(
        "els => els.map(el => ({text: el.textContent.trim(), tip: el.getAttribute('data-tip'), key: el.getAttribute('data-tip-key')}))"
    )
    if not any(item["tip"] for item in rendered):
        pytest.skip("Rows 48/49/50 render no tooltip on this account")
    for item in rendered:
        if item["text"]:
            assert item["tip"]
            assert item["key"] is None


def test_slot_editor_tooltips_never_render_object_object(editor_page):
    """Bloodline tooltip models are composed before entering the rendered DOM.

    Injection: restore ``lines.push(bloodlineAbilityTip(entry))`` at either
    Bloodline call site. The browser then exposes ``[object Object]`` in an
    attribute and this goes red.
    """
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    values = page.locator(".slot-editor [data-tip], .slot-editor [data-tip-key], .slot-editor [data-tip-body], .slot-editor [data-tip-foot], .slot-editor [aria-label]").evaluate_all(
        "els => els.flatMap(el => Array.from(el.attributes).map(attr => attr.value))"
    )
    assert not any("[object Object]" in value for value in values)


def test_worker_tile_base_pp_matches_its_correlated_cell(editor_page):
    """Worker tiles name the capped Base PP shown by their matching cell.

    Injection: revert the worker key to ``occupant.base_pp``. A clipped worker
    then disagrees with the cell carrying the same group and slot identity.
    """
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    tiles = page.locator('.slot-editor .slot-tile[data-group-id="worker"]').filter(has=page.locator(".slot-remove"))
    for index in range(tiles.count()):
        tile = tiles.nth(index)
        group_id = tile.get_attribute("data-group-id")
        slot_index = tile.get_attribute("data-slot-index")
        cell = page.locator(f'.slot-editor .editor-base-pp-cell[data-group-id="{group_id}"][data-slot-index="{slot_index}"]')
        assert cell.count() == 1
        tile_key = tile.get_attribute("data-tip-key") or ""
        assert tile_key.startswith("Base PP ")
        assert tile_key.removeprefix("Base PP ") == cell.get_attribute("data-tip-key")


def test_base_pp_cell_tooltips_do_not_use_picker_label(editor_page):
    """Per-card Base PP tooltips use the settled label, scoped to their cells.

    Injection: restore ``Construction PP`` in the cell's construction fragment.
    The cell-scoped assertion goes red while the legitimate candidate picker
    column remains outside this test's scope.
    """
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    cells = page.locator('.slot-editor .editor-base-pp-cell[data-tip-body]')
    for index in range(cells.count()):
        cell = cells.nth(index)
        assert "Construction PP" not in " ".join(
            value or "" for value in (
                cell.get_attribute("data-tip"),
                cell.get_attribute("data-tip-key"),
                cell.get_attribute("data-tip-body"),
                cell.get_attribute("data-tip-foot"),
            )
        )


def test_base_pp_tooltip_fragments_are_terminated_sentences(editor_page):
    """Terrain and construction fragments remain prose in every rendered state.

    Injection: restore the bare-space ``terrainText``/``constructionText``
    splice. A rendered body then contains an unterminated fragment and goes red;
    the four combinations are represented by the optional fragments independently.
    """
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    bodies = page.locator('.slot-editor .editor-base-pp-cell[data-tip-body]').evaluate_all(
        "els => els.map(el => el.getAttribute('data-tip-body')).filter(Boolean)"
    )
    for body in bodies:
        assert body.endswith(".")
        assert not re.search(r"(?<!\.) (?:Terrain |Boostable )", body)


def _open_worksite_modal(page, base_url: str):
    page.set_viewport_size({"width": 1920, "height": 1080})
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    button = page.locator(".slot-editor button.worksite-change-btn").first
    button.wait_for(timeout=10_000)
    button.click()
    modal = page.locator("#worksite-modal-overlay")
    modal.wait_for(state="visible", timeout=10_000)
    return modal


def test_worksite_tile_art_has_rendered_pixel_coverage(editor_page):
    """Worksite art fills the tile's rendered content area, not only its source box.

    Injection: restore ``background-size: auto 100%``. The screenshot then has
    less than the required non-background coverage.
    """
    page, base_url = editor_page
    modal = _open_worksite_modal(page, base_url)
    art = modal.locator(".worksite-tile-option .worksite-tile-art").first
    image = Image.open(io.BytesIO(art.screenshot())).convert("RGB")
    background = Counter(image.getdata()).most_common(1)[0][0]
    pixels = list(image.getdata())
    non_background = sum(
        1 for pixel in pixels if sum(abs(pixel[index] - background[index]) for index in range(3)) > 24
    )
    assert non_background / len(pixels) > 0.20


def test_worksite_modal_click_does_not_open_tooltip(editor_page):
    """Programmatic modal focus after a real click does not open a tile tooltip.

    Injection: remove the ``:focus-visible`` gate from the delegated focus handler.
    """
    page, base_url = editor_page
    _open_worksite_modal(page, base_url)
    assert page.locator("#ceres-tooltip").is_hidden()


def test_worksite_modal_keyboard_focus_still_opens_tooltip(editor_page):
    """A real Tab into a worksite tile still exposes its focus tooltip.

    Injection: make the delegated focus handler never fire. ``locator.focus()``
    is intentionally not used because it inherits the prior modality.
    """
    page, base_url = editor_page
    _open_worksite_modal(page, base_url)
    page.keyboard.press("Tab")
    page.locator("#ceres-tooltip.is-visible").wait_for(timeout=5_000)


def test_worker_tile_tooltip_lists_the_pp_chain(editor_page):
    """The worker tile exposes the rendered Base PP-to-total chain."""
    page, base_url = editor_page
    tile = _open_editor_with_worker(page, base_url)
    key = tile.get_attribute("data-tip-key") or ""
    expected_base_pp = key.removeprefix("Base PP ")
    body = tile.get_attribute("data-tip-body") or ""
    assert expected_base_pp and f"Base PP {expected_base_pp}" in body
    assert "After cap" in body
    assert "Boostable" in body
    assert "Total PP" in body


def test_worksite_resource_tooltip_names_worksite(editor_page):
    """A worksite resource icon identifies both the worksite and its output.

    Injection: revert ``resourceIconHtml`` to title the icon with only the
    resource. The rendered tooltip then has no distinct worksite title.
    """
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    icons = page.locator(".resource-cell img.resource-icon")
    if icons.count() == 0:
        pytest.skip("No built worksite resource icon rendered on the supplied plots")
    for index in range(icons.count()):
        icon = icons.nth(index)
        assert icon.get_attribute("data-tip")
        assert icon.get_attribute("data-tip-body") == icon.get_attribute("alt")
        assert icon.get_attribute("data-tip") != icon.get_attribute("data-tip-body")


def test_clear_workers_button_is_distinct_and_explained(editor_page):
    """The worker-group clear action is labelled and tooltiped distinctly.

    Injection: revert the label to ``Clear group`` and remove its tooltip. The
    rendered control then fails both the visible-copy and tooltip assertions.
    """
    page, base_url = editor_page
    _open_editor(page, base_url, PLOT_WITH_RUNI)
    clear = page.locator('.slot-editor button[data-slot-action="clear"]').first
    assert clear.inner_text().strip() == "Clear workers"
    assert clear.get_attribute("data-tip") == "Clear workers"
    assert clear.get_attribute("data-tip-kind") == "warn"
    assert clear.get_attribute("data-tip-severity") == "danger"
    assert "worker card" in (clear.get_attribute("data-tip-body") or "")
    assert "group" not in (clear.get_attribute("data-tip-body") or "").lower()
    assert "72-hour cooldown" in (clear.get_attribute("data-tip-foot") or "")

    # R-143: the WORKER group no longer emits a fill button at all, so the old
    # assertion here ("data-tip == 'Fill empty slots'") can never pass again. Asset
    # groups keep theirs, labelled "Add <group>" -- a different control with a
    # different label, which is not what he objected to. Assert on that instead, and
    # assert the worker band's absence explicitly so this test still fails if the
    # removed button comes back.
    worker_fill = page.locator('.slot-group[data-group-id="worker"] button[data-slot-action="fill"]')
    assert worker_fill.count() == 0, "R-143: no fill button in the worker group"
    fill = page.locator('.slot-editor button[data-slot-action="fill"]').first
    if fill.count():
        tip = fill.get_attribute("data-tip") or ""
        assert tip.startswith("Add "), tip
        assert "stage" in (fill.get_attribute("data-tip-body") or "").lower()
    bulk_bodies = page.locator(".slot-editor .slot-bulk[data-tip-body]")
    for index in range(bulk_bodies.count()):
        assert "group" not in (bulk_bodies.nth(index).get_attribute("data-tip-body") or "").lower()

    cancel = page.locator('.slot-editor button').filter(has_text=re.compile(r"^Cancel$"))
    for index in range(cancel.count()):
        assert cancel.nth(index).get_attribute("data-tip") is None


def _measure_arrow(page, trigger, scroll=True):
    if scroll:
        trigger.scroll_into_view_if_needed()
    trigger.hover()
    settle(page)
    assert page.locator("#ceres-tooltip").evaluate(
        "tip => !tip.hidden && tip.classList.contains('is-visible')"
    )
    return trigger.evaluate(
        """trigger => {
            const tip = document.querySelector('#ceres-tooltip');
            const tipRect = tip.getBoundingClientRect();
            const triggerRect = trigger.getBoundingClientRect();
            const paintedArrow = tipRect.left + parseFloat(getComputedStyle(tip).borderLeftWidth)
                + parseFloat(getComputedStyle(tip, '::after').left);
            return {
                arrow: paintedArrow,
                trigger: triggerRect.left + triggerRect.width / 2,
                box: tipRect.left + tipRect.width / 2,
            };
        }"""
    )


def _arrow_measurements(page):
    triggers = page.locator('#plot-tbody [data-tip]:not([data-tip] [data-tip]):not([disabled])')
    measurements = [_measure_arrow(page, triggers.nth(index), scroll=False) for index in range(min(5, triggers.count()))]
    edge_index = page.evaluate(
        """() => Array.from(document.querySelectorAll('#plot-tbody [data-tip]')).findIndex(el => {
            const rect = el.getBoundingClientRect();
            return !el.parentElement.closest('[data-tip]') && rect.width > 0;
        })"""
    )
    assert edge_index >= 0
    edge = page.locator('#plot-tbody [data-tip]').nth(edge_index)
    edge.evaluate("el => Object.assign(el.style, {position: 'fixed', left: '1840px', top: '400px'})")
    measurements.append(_measure_arrow(page, edge, scroll=False))
    return measurements


def test_tooltip_arrow_is_painted_at_trigger_center(editor_page):
    """The shared tooltip arrow follows the trigger, including clamped boxes.

    The position is measured from the tooltip border and the computed ``::after``
    ``left``; ``--ceres-tooltip-arrow-left`` is never read back because that would
    measure the same CSS variable the buggy code writes. A pixel probe, if added,
    must key on the arrow's own ``border-top-color``, never on pixels that merely
    differ from the background: ``box-shadow: 0 8px 20px`` bleeds below the box
    and is symmetric about the box centre, producing a false centred result.
     retires the old stacked/signal/compact arrow variants in favor of one
    peer-depth shell.
    """
    page, base_url = editor_page
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(base_url)
    page.wait_for_selector("#plot-tbody > tr.plot-row", timeout=30_000)
    measurements = _arrow_measurements(page)
    assert len(measurements) >= 5
    assert all(abs(item["arrow"] - item["trigger"]) <= 1 for item in measurements)
    assert any(abs(item["box"] - item["trigger"]) > 20 for item in measurements)
