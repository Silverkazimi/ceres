"""Coverage for the "click an empty tile to bring back a removed card" feature
(2026-08-25, `work/ceres-wave2/queue.md`). Mirrors the v0.5.20 undo --
this is the mirror case: cancelling a staged REMOVAL via the tile it emptied,
rather than a staged ADD via the tile it occupies.

 (Batch C, , row 31): the two remaining subshell-slice tests are
converted. `test_restore_action_undoes_staged_removal_without_consulting_mixed_guard`
moves to the live fixture: stage a real removal on a real occupied worker slot (the
same `.slot-remove` interaction `test_empty_tile_renders_add_when_genuinely_empty_and_restore_when_pending_removal`
below already drives), then click the resulting ghost tile's own "restore" action and
read the occupant back. `test_restore_action_with_no_matching_stage_entry_is_a_silent_noop`
stays a `vm_context` whole-module test -- no live-DOM equivalent exists: the "restore"
control (`renderEditorTile`'s `is-pending-removal` branch) only ever renders when
`CERES_STAGE` already holds the matching entry, so a real user session cannot reach a
"restore" click with no matching entry; see that test's own docstring.
"""

from __future__ import annotations

import json
import urllib.request

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres
from .vm_context import run_in_module_context


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def test_restore_action_undoes_staged_removal_without_consulting_mixed_guard(
    live_browser: tuple[Browser, str],
) -> None:
    """Named injection (watched red by an independent verifier, 2026-09-02): in
    `handleSlotEditorClick()`'s `slotAction === 'restore'` branch, replace
    `if (stagedEntry) await removeCheckoutStage(stagedEntry);` with a call through the
    mixed-guarded removal path instead
    (`await stagePricedRemovals(row, [{group, index, occupant: stagedEntry._previous_asset}], '')`)
    -- the restore click now fires a `/removal-impact` request (the checkout-undo path
    never does) and the ghost tile does not reliably return to its exact previous
    occupant; this test fails on the request-count assertion."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    deed_uid = None
    for row in board["plots"]:
        cap = row.get("capacity", {}) or {}
        if cap.get("worker_count", 0) >= 1:
            deed_uid = row["deed_uid"]
            break
    assert deed_uid, "no live plot has an occupied worker slot to stage a removal against"

    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    editor = page.locator(f"tr.expand-row[data-deed-uid='{deed_uid}'] .slot-editor")
    editor.wait_for(timeout=30_000)
    worker = editor.locator(".slot-group[data-group-id='worker']")

    occupied_tile = worker.locator(".slot-tile:not(.is-empty):not(.is-staged)").first
    occupied_tile.wait_for(timeout=30_000)
    occupant_name = (occupied_tile.locator(".slot-name").text_content() or "").strip()
    assert occupant_name, "occupied tile carries no readable name to verify the restore against"

    occupied_tile.hover()
    with page.expect_request("**/removal-impact*", timeout=30_000):
        occupied_tile.locator(".slot-remove").click()
    ghost = worker.locator(".slot-tile.is-pending-removal")
    ghost.wait_for(timeout=30_000)
    assert ghost.get_attribute("data-slot-action") == "restore"
    assert ghost.locator(".slot-restore-label").count() == 0

    removal_impact_requests: list[str] = []
    page.on("request", lambda req: removal_impact_requests.append(req.url) if "removal-impact" in req.url else None)
    toast = page.locator("#ceres-stage-toast")

    ghost.click()
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('.slot-tile')).some((el) => el.classList.contains('is-pending-removal'))",
        timeout=30_000,
    )
    restored_tile = worker.locator(".slot-tile:not(.is-empty):not(.is-staged)").filter(has_text=occupant_name)
    assert restored_tile.count() == 1, f"the original occupant {occupant_name!r} did not come back after restore"
    assert not removal_impact_requests, "restore must use the checkout-undo path, never price a removal"
    if toast.count():
        assert "staged change in the other direction" not in toast.inner_text(), "restore must never surface the mixed-direction guard's message"
    page.close()


def test_restore_action_with_no_matching_stage_entry_is_a_silent_noop() -> None:
    """ (row 31): converted from a `_slice`+hand-stub subshell harness to
    `vm_context` -- calls the REAL `handleSlotEditorClick`/`editorGroups` (no
    dependency-stub list to go stale), with `CERES_STAGE` emptied and
    `removeCheckoutStage` overridden only to detect whether it is (wrongly) called.

    No live-DOM equivalent exists: the "restore" control only ever renders when
    `renderEditorTile` is already in its `is-pending-removal` branch, which by
    construction always has a matching `CERES_STAGE` entry (proven by the sibling
    test above and by `test_empty_tile_renders_add_when_genuinely_empty_and_restore_when_pending_removal`
    below) -- so a "restore" click with no matching entry has no click path a real
    user session can reach. This stays a defensive-code-path test, same reasoning
    this ticket's own `test_autocapture_frontend.py` G4 deviation documents."""
    body_js = """
CERES_STAGE.length = 0;
BOARD = { plots: [{ deed_uid: 'plot', capacity: { potential_card_capacity: 1 }, workers: [], bonus: {}, staking_facts: {} }] };
let undoCalls = 0;
removeCheckoutStage = async () => { undoCalls += 1; };
function click(action) {
  return { target: { closest(selector) { return selector === '[data-slot-action]' ? action : null; } }, preventDefault() {}, stopPropagation() {} };
}
(async () => {
  await handleSlotEditorClick(click({ dataset: { slotAction: 'restore', deedUid: 'plot', groupId: 'worker', slotIndex: '0' } }));
  if (undoCalls !== 0) throw new Error('handleSlotEditorClick called removeCheckoutStage with no matching staged entry -- undoCalls=' + undoCalls);
})();
"""
    run_in_module_context(body_js)


# (Batch C, , row 31): migrated in from a source-level slice
# whose stub list drifted (`ReferenceError: foilBadgeHtml is not defined` -- confirmed
# `renderEditorTile`'s restore branch, app.js:3796-3805, calls `foilBadgeHtml(previous, ...)`
# on the ghosted asset, which the old stub list omitted, same Class-A stale-stub failure as
# the rest of this campaign). `renderEditorTile` now has TWO non-add empty-slot branches
# (app.js:3794-3810): `is-pending-removal`/`data-slot-action="restore"` for a slot emptied by
# a staged removal (case (b) here), and `is-pending-add` for an in-flight staged ADD
# (unrelated to this feature -- asserted NOT present, to prove the right branch is being hit).
# `tests/ceres/test_live_restore_tile.py` already drives the full restore click path against
# two REAL plots (bloodline pair-break, sole-power-source hazard) -- this rendered replacement
# additionally proves case (a), the plain disambiguation this file's own docstring exists for
# a genuinely empty slot (no staged removal at all) must keep the ordinary "add" affordance.
def test_empty_tile_renders_add_when_genuinely_empty_and_restore_when_pending_removal(live_browser: tuple[Browser, str]) -> None:
    """Named injection (watched red by an independent verifier, 2026-09-02): in renderEditorTile(), mangle the `is-pending-removal` class literal served via page.route — `.is-pending-removal` never appears; this test fails."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    deed_uid = None
    for row in board["plots"]:
        cap = row.get("capacity", {}) or {}
        if cap.get("worker_count", 0) >= 1 and cap.get("open_slots", 0) >= 1:
            deed_uid = row["deed_uid"]
            break
    assert deed_uid, "no live plot has both an existing worker occupant and a genuinely empty slot"

    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")

    # Case (a): a genuinely empty slot, untouched, keeps the ordinary "add" affordance.
    empty_tile = worker.locator(".slot-tile.is-empty").first
    empty_tile.wait_for(timeout=30_000)
    assert empty_tile.get_attribute("data-slot-action") == "add"
    assert "is-pending-removal" not in (empty_tile.get_attribute("class") or "")
    assert "is-pending-add" not in (empty_tile.get_attribute("class") or "")

    # Case (b): stage a removal on the real occupant, then the emptied slot must ghost
    # the removed card back in with the restore affordance -- never plain "add".
    occupied_tile = worker.locator(".slot-tile:not(.is-empty):not(.is-staged)").first
    occupied_tile.wait_for(timeout=30_000)
    occupant_name = occupied_tile.locator(".slot-name").text_content()
    occupied_tile.hover()
    occupied_tile.locator(".slot-remove").click()

    ghost = worker.locator(".slot-tile.is-pending-removal")
    ghost.wait_for(timeout=30_000)
    assert ghost.get_attribute("data-slot-action") == "restore"
    assert "is-empty" not in (ghost.get_attribute("class") or "")
    assert "is-pending-add" not in (ghost.get_attribute("class") or "")
    if occupant_name:
        assert occupant_name.strip() in ghost.inner_text()
    page.close()
