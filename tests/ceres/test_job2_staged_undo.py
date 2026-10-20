""" (Batch C, , row 25): both remaining `_slice`+hand-stub
subshell-run tests are converted to the live fixture. The third test in this file
(`test_real_worker_undo_reannotates_solo_bloodline_holder`) was already live before this
ticket and is unchanged -- it is the model these two conversions follow (route-mocked
candidates, real click/hover interactions, DOM state read back).

`test_staged_tile_remove_uses_checkout_undo_and_live_opposite_direction_stages`'s
real failure was `ReferenceError: bloodlinePairSubTypes is not defined` in the sibling
test, but this test's OWN slice (`handleSlotEditorClick` + `plotPoweredAfterStage`) was
passing at conversion time -- migrated anyway per the ticket's scope (row 25's whole file
is Batch C). Converted to: (a) staging an ADD via the real picker, then undoing it
through its own tile's "remove" button -- proves the checkout-undo path
(`removeCheckoutStage`), never the removal-impact pricing endpoint; (b) with that ADD
still staged (an "incoming" stage on the deed), clicking "remove" on a LIVE, unstaged
occupant on the SAME deed -- proves R-45's maintainer-directed removal path prices and stages
the opposite-direction move too.

`test_checkout_undo_all_restores_every_plot_and_requires_confirmation` is converted to:
staging one ADD each on two different plots, then driving the real "Undo all" checkout
button, which calls the browser's native `confirm()` -- accepted (both plots' tiles
revert) and dismissed (the staged tile survives) are both driven through Playwright's
`page.on('dialog', ...)`. The per-move `.checkout-remove` click (`removeCheckoutStage`
called directly, bypassing `undoAllCheckoutStage()`) is dropped as redundant, not
retargeted: it is the exact same `removeCheckoutStage` call the sibling test above
already proves by clicking a staged tile's own remove button -- the checkout panel's
per-move button and a staged tile's own button are two DOM entry points into the one
function, and the first conversion already demonstrates the function itself works.


: the undo-all test that counted the old checkout panel's `.checkout-plot-group` / `.checkout-move-delta` rows was retired; the Changes modal () renders no such elements and the dead CSS went with it.
"""

from __future__ import annotations

import json
import urllib.request

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _stage_candidate(page: Page, worker_locator, candidate: dict) -> None:
    pattern = "**/api/plots/*/candidates*"
    mock_payload = json.dumps({"candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []})
    page.route(pattern, lambda route: route.fulfill(status=200, content_type="application/json", body=mock_payload))
    empty = worker_locator.locator(".slot-tile.is-empty").first
    with page.expect_request(pattern):
        empty.click()
    candidate_row = page.locator(".candidate-row").first
    candidate_row.wait_for(timeout=30_000)
    candidate_row.click()
    page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30_000)
    page.unroute(pattern)


def test_staged_add_undo_uses_checkout_path_and_live_opposite_direction_stages(
    live_browser: tuple[Browser, str],
) -> None:
    """R-45 maintainer ruling: an opposite-direction removal is priced and staged while an
 incoming change remains staged on the same plot."""
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
    assert deed_uid, "no live plot has both an occupied worker slot and an open one"

    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")

    removal_impact_requests: list[str] = []
    page.on("request", lambda req: removal_impact_requests.append(req.url) if "removal-impact" in req.url else None)

    candidate = {"name": "HK Undo Candidate", "base_pp": 1000, "level": 1, "bucket": "idle", "land_abilities": []}
    _stage_candidate(page, worker, candidate)
    staged_tile = worker.locator(".slot-tile.is-staged").filter(has_text="HK Undo Candidate")
    staged_tile.wait_for(timeout=30_000)

    # The ADD above is still staged -- an "incoming" stage on this deed. Clicking
    # "remove" on a LIVE, unstaged occupant on the SAME deed is priced and staged.
    live_occupant = worker.locator(".slot-tile:not(.is-empty):not(.is-staged)").first
    live_occupant.wait_for(timeout=30_000)
    live_name = live_occupant.inner_text()
    live_occupant.hover()
    live_occupant.locator(".slot-remove").click()
    pending_removal = worker.locator(".slot-tile.is-pending-removal")
    pending_removal.wait_for(timeout=30_000)
    assert live_name.splitlines()[0] in pending_removal.inner_text()
    assert removal_impact_requests, "the opposite-direction removal must be priced before staging"

    # (a) Undo the staged ADD through its own tile -- the checkout-undo path
    # (removeCheckoutStage), never the removal-impact pricing endpoint.
    staged_tile.hover()
    staged_tile.locator(".slot-remove").click()
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('.slot-tile')).some((el) => el.textContent.includes('HK Undo Candidate'))",
        timeout=30_000,
    )
    assert len(removal_impact_requests) == 1, "undoing a staged ADD must use the checkout-undo path, never price another removal"
    page.close()


# (Batch C, , row 25): migrated in from a source-level slice.
# Real failure was `ReferenceError: bloodlinePairSubTypes is not defined` -- confirmed
# (app.js:2017-2027 comment) renamed it `bloodlinePlotSubTypes`, a Class-A stale-name
# stub, not a product break. Retargeted to a rendered read: two synthetic worker candidates
# sharing a BLOODLINE `sub_type` ('Elf') are staged into two empty slots of the SAME real
# plot through the real picker (mutateEditorSlot -> annotateEditorBloodlines runs on every
# stage, exactly as production code does), proving the holder's `.slot-bloodline-badge`
# flips PAIRED once the companion lands; the companion's staged ADD is then undone (its own
# `.slot-remove`, the v0.5.20 undo-a-staged-add path -- same mechanism as the sibling test
# above, `removeCheckoutStage`), and the holder's badge must flip back to SOLO.
def test_real_worker_undo_reannotates_solo_bloodline_holder(live_browser: tuple[Browser, str]) -> None:
    """Named injection (watched red by an independent verifier, 2026-09-02): in annotateEditorBloodlines(), replace the companion-lookup line with `const companions = [];` served via page.route — the holder's badge stays SOLO instead of flipping PAIRED; this test fails."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    deed_uid = None
    for row in board["plots"]:
        if (row.get("capacity", {}) or {}).get("open_slots", 0) >= 2:
            deed_uid = row["deed_uid"]
            break
    assert deed_uid, "no live plot has two open worker slots to stage a synthetic bloodline pair into"

    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")

    def stage_candidate(candidate: dict) -> None:
        pattern = "**/api/plots/*/candidates*"
        mock_payload = json.dumps({"candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []})
        page.route(pattern, lambda route: route.fulfill(status=200, content_type="application/json", body=mock_payload))
        empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
        with page.expect_request(pattern):
            empty.click()
        row = page.locator(".candidate-row").first
        row.wait_for(timeout=30_000)
        row.click()
        page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30_000)
        page.unroute(pattern)

    holder = {
        "name": "HK Solo Holder", "base_pp": 2000, "level": 1, "bucket": "idle",
        "land_abilities": [["BLOODLINE", 0.3, "Elf", {"active": False, "companions": []}]],
    }
    stage_candidate(holder)
    holder_tile = worker.locator(".slot-tile.is-staged").filter(has_text="HK Solo Holder")
    holder_tile.wait_for(timeout=30_000)
    assert holder_tile.locator(".slot-bloodline-badge").inner_text().strip() == "SOLO"

    companion = {"name": "HK Elf Companion", "base_pp": 2000, "level": 1, "bucket": "idle", "sub_type": "Elf", "land_abilities": []}
    stage_candidate(companion)
    companion_tile = worker.locator(".slot-tile.is-staged").filter(has_text="HK Elf Companion")
    companion_tile.wait_for(timeout=30_000)
    # Staging the companion re-annotates the holder live -- PAIRED now, no page reload.
    assert holder_tile.locator(".slot-bloodline-badge").inner_text().strip() == "PAIRED"

    companion_tile.hover()
    companion_tile.locator(".slot-remove").click()
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('.slot-tile')).some((el) => el.textContent.includes('HK Elf Companion'))",
        timeout=30_000,
    )
    assert holder_tile.locator(".slot-bloodline-badge").inner_text().strip() == "SOLO", "undoing the staged companion add must re-flip the holder back to SOLO"
    page.close()
