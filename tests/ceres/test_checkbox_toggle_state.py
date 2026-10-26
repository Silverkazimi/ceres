"""Guard for the checkbox-toggle inversion in handleCombineAction (app.js).

A checkbox's pre-click activation steps flip `.checked` BEFORE the click event
is dispatched; handleSlotEditorClick's preventDefault() runs the canceled
activation steps that revert it, but only AFTER dispatch completes -- so
`action.checked`, read inside this bubbled handler, is already the POST-toggle
value. Reading it through a `!` (as the code did from e47624ba, v0.7.31,
onward -- this has never worked) recomputes the pre-click state and rewrites
the model to what it already held, so the box always repaints ticked. This
covered two controls originally: the "Upgradeable only" idle-cards filter and
the combine-dialog's per-copy checkboxes. The combine-dialog checkboxes were
RETIRED in v0.7.73 (work/combine-modal-redesign/redesign-plan.html) -- the
'toggle-copy' action no longer exists, replaced by a quantity control
(combineApplyQuantity/'preset'/'qty' in app.js) that never uses a checkbox's
`.checked` at all. The "Upgradeable only" filter (and the whole "Idle cards"
section it lived on) was removed in full 2026-08-30 -- its own toggle-
inversion guard is retired along with it below, since there is no longer a
`toggle-upgradeable` action or an `IDLE_UPGRADEABLE_ONLY` state to invert.
Only the retirement guard for the earlier 'toggle-copy' action remains.

/census row 8 (batch B): the placement guard below was a pure
source-text scan (`_slice` + substring, no execution). Converted per plan
("cheap regardless -- drop the substring, add one rendered read") to a real
render of the combine dialog: no checkbox control exists anywhere in it, and
the real quantity control (`preset`/`qty`) does.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from tests.ceres.combine_door import open_combine_by_print, serve_idle_cards
from tests.ceres.conftest import spawn_live_ceres

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_occupied_worker(base_url: str) -> tuple[str, dict]:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if not (plot.get("capacity") or {}).get("worker_count", 0):
            continue
        deed_uid = plot["deed_uid"]
        with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
            row = json.loads(response.read().decode("utf-8"))
        for worker in row.get("workers", []):
            if (
                worker.get("card_detail_id") is not None
                and worker.get("edition") is not None
                and worker.get("bcx_max") is not None
                and worker.get("bcx") is not None
                and worker["bcx"] < worker["bcx_max"]
            ):
                return deed_uid, worker
    raise AssertionError("no live plot has an unmaxed worker occupant to build a combine fixture from")


def test_rendered_combine_dialog_has_no_checkbox_control_only_the_quantity_control(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 8: replaces the placement guard that scanned
    `handleCombineAction()`'s source text for the absence of
    `kind === 'toggle-copy'`/`kind === 'toggle-upgradeable'` and the presence of
    the `data-combine-action="preset"`/`"qty"` markup strings. Opens the real
    combine dialog and reads the real DOM instead: zero `input[type=checkbox]`
    anywhere inside it, and the real preset/qty buttons present.

    Named injection (not applied here; independently watched red/green per
    ): `renderCombineDialogEditor()`'s amount control markup changed from
    the preset/stepper buttons to
    `<input type=\"checkbox\" class=\"combine-copy-checkbox\">` per copy row
    (reintroducing the retired toggle-copy checkbox UI). Expected red: the
    rendered dialog now contains an `input[type=checkbox]`, where this test
    asserts a count of 0."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid, occupant = _real_occupied_worker(base_url)
    duplicate_one = dict(occupant)
    duplicate_one["uid"] = "C-TEST-CHECKBOX-DUP1"
    duplicate_one["asset_ref"] = "checkbox-dup1"
    duplicate_one["bcx"] = 1
    duplicate_one["available"] = True
    duplicate_one["blocked_reason"] = None
    duplicate_one["bucket"] = "stake_expired_or_historical"
    duplicate_two = dict(duplicate_one)
    duplicate_two["uid"] = "C-TEST-CHECKBOX-DUP2"
    duplicate_two["asset_ref"] = "checkbox-dup2"

    def fulfill_preview(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "account": "player0005", "uids": [duplicate_one["uid"], duplicate_two["uid"]],
            "predicted_survivor_uid": duplicate_one["uid"], "resulting_bcx": 2,
            "hazard": {"destroyed_uids": [duplicate_two["uid"]]}, "buildable": True,
            "observed_at": "2026-09-02T00:00:00Z",
        }))

    serve_idle_cards(page, [duplicate_one, duplicate_two])
    page.route("**/api/combine/preview*", fulfill_preview)
    open_combine_by_print(page, base_url, occupant)
    overlay = page.locator("#combine-dialog-overlay")
    overlay.wait_for(state="visible", timeout=30_000)

    assert overlay.locator("input[type=checkbox]").count() == 0, (
        "the retired per-copy checkbox control must never reappear in the combine dialog"
    )
    overlay.locator('[data-combine-action="preset"]').first.wait_for(timeout=10_000)
    assert overlay.locator('[data-combine-action="qty"]').count() >= 1, (
        "the real quantity stepper control must be present"
    )
    page.close()
