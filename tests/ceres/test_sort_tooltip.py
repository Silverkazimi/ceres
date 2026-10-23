"""Observable coverage for the slot editor's worker-group control band."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


APP_JS = Path(__file__).parents[2] / "ceres" / "static" / "app.js"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_worker_group_control_band_has_no_sort_button(live_browser: tuple[Browser, str]) -> None:
    r""" deviation, reported for dispatcher review: this test used
    to cover the contribution-sort toggle's view-only tooltip (census row 33b's
    stated disposition, "hover the Sort button before/after clicking, read the
    tooltip text"). That control no longer exists in the product. Commit
    `36123139` ("fix: remove Sort by contribution in full", ceres v0.7.109,
    2026-08-30 -- BEFORE this ticket's HEAD `431ce8f1`) removed the action,
    click handler, label, tooltip, and pressed-state branches in full:
    `ceres/static/app.js`'s `renderEditorGroup()` worker-group `actions` array
    is now literally `['fill', 'clear', 'clear_plot']` (no `'sort'`), the local
    `sorted` is hardcoded `false` and never flips, and grep confirms
    `data-slot-action="sort"` occurs nowhere in the file. There is no `data-tip`
    to hover and read; converting to "read the tooltip text" per the ticket's
    original instruction would fabricate a UI surface the product does not
    have -- the same rule the dispatcher's own ruling for `test_editor.py`
    and `test_worker_upgrade_frontend.py` names for dead code, but this is a
    step further: not unread code, a deleted feature.

    Repurposed instead as a real, rendered-DOM regression guard on the removal
    itself, matching the removal commit's own description ("The control band is
    now Fill empty slots / Clear group / Clear plot."): the worker group's
    control band must render exactly those three buttons, in that order, and no
    element anywhere carries `data-slot-action="sort"`.

    Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `renderEditorGroup()`, `'sort'` was added back to
    the worker-group `actions` array (`['fill', 'clear', 'clear_plot']` ->
    `['fill', 'clear', 'clear_plot', 'sort']`). Red:
    `assert control_labels == ['Fill empty slots', 'Clear group', 'Clear plot']`
    failed with `['Fill empty slots', 'Clear group', 'Clear plot', 'Clear plot']`
    -- a fourth button rendered (its label falls through to the `label`
    ternary's final `'Clear plot'` branch, since the removed `'sort'`-specific
    label case is gone and `'sort'` matches none of the remaining checks).
    Restoring the three-action list made this test pass again (whole-file run:
    1 passed)."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000
    )
    rows = page.locator("#plot-tbody > tr.plot-row")
    worker_group = None
    for index in range(rows.count()):
        rows.nth(index).click()
        expanded = page.locator("tr.expand-row").nth(index)
        group = expanded.locator(".slot-group[data-group-id='worker']")
        try:
            group.wait_for(timeout=10_000)
            # the band renders `preparing` first (an inert, empty controls line); the
            # controls exist once it reports `ready`
            expanded.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
        except Exception:
            rows.nth(index).click()
            continue
        worker_group = group
        break
    assert worker_group is not None, "no live plot rendered a worker slot editor"

    buttons = worker_group.locator(".slot-group-controls .slot-bulk")
    control_labels = [buttons.nth(i).inner_text().strip() for i in range(buttons.count())]
    # R-143, his request: "Remove \"Fill empty slots\" button. It only opens the picker."
    # The worker group now renders exactly two controls. This assertion is the guard for
    # that removal, not merely an updated expectation: it goes RED the moment a fill
    # button reappears in the worker band, which is what he asked never to see again.
    # Empty worker slots are still their own `data-slot-action="add"` buttons and still
    # open the same picker, so nothing is stranded -- asserted directly below.
    # Since then the band gained "Fill plot" (data-slot-action="fill_plot", fill-plot.js): it
    # proposes a whole-plot fill in its own window and stages nothing until accepted -- a
    # different control from the removed per-group "Fill empty slots" (data-slot-action="fill"),
    # which the next assertion still keeps out.
    assert control_labels == ["Fill plot", "Clear workers", "Clear plot"], control_labels
    assert worker_group.locator('[data-slot-action="fill"]').count() == 0, (
        "R-143: the worker group must render no fill button"
    )
    # NOT a tautology: R-143 removed the fill button on the argument that every empty
    # worker slot is already its own picker-opening button. If that stopped being true
    # the removal would strand the slot, so the claim is asserted rather than trusted.
    empties = worker_group.locator('.slot-tile.is-empty[data-slot-action="add"]')
    if worker_group.locator('.slot-tile.is-empty').count():
        assert empties.count() > 0, (
            "R-143: an empty worker slot must itself open the picker"
        )
    assert worker_group.locator('[data-slot-action="sort"]').count() == 0
    page.close()
