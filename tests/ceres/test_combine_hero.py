"""Contract guards for the compact full-size-art combine editor.

/census row 12 (batch B): the file's four tests all scanned `app.js`'s
`renderCombineDialogEditor()` source for literal markup fragments (a copy of
the dialog's HTML template, effectively re-typed inside this test file). That
breaks on any cosmetic edit, not just a real regression -- replaced with one
rendered read of the real combine dialog, opened through the board's `?combine=`
door (tests/ceres/combine_door.py) against route-mocked idle-duplicate/preview data
(no server-side combine state needed -- this is a pure frontend-render contract)."""

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
NOTICES = ROOT / "ceres/notices.py"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_occupied_worker(base_url: str) -> tuple[str, dict, int]:
    """A real plot with a real, combinable (unmaxed, resolved-ceiling, non-Runi)
    worker occupant. Returns (deed_uid, occupant, slot_index)."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if not (plot.get("capacity") or {}).get("worker_count", 0):
            continue
        deed_uid = plot["deed_uid"]
        with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
            row = json.loads(response.read().decode("utf-8"))
        for index, worker in enumerate(row.get("workers", [])):
            if (
                worker.get("card_detail_id") is not None
                and worker.get("edition") is not None
                and worker.get("bcx_max") is not None
                and worker.get("bcx") is not None
                and worker["bcx"] < worker["bcx_max"]
            ):
                return deed_uid, worker, index
    raise AssertionError("no live plot has an unmaxed worker occupant to build a combine fixture from")


def _open_combine_dialog(page, base_url: str) -> tuple[str, dict]:
    """Route-mocks combinable idle duplicates of a real staked worker, opens the
    dialog for that printing through the board's `?combine=` door, and returns
    (deed_uid, occupant) once the real `#combine-dialog-overlay`
    (`renderCombineDialogEditor`) is visible."""
    deed_uid, occupant, slot_index = _real_occupied_worker(base_url)
    # The staged occupant is never part of IDLE_CARDS_CACHE, so the dialog's
    # copies are the idle duplicates alone: two are the minimum it opens on.
    duplicate_one = dict(occupant)
    duplicate_one["uid"] = "C-TEST-COMBINE-HERO-DUP1"
    duplicate_one["asset_ref"] = "combine-hero-dup1"
    duplicate_one["bcx"] = 1
    duplicate_one["available"] = True
    duplicate_one["blocked_reason"] = None
    duplicate_one["bucket"] = "stake_expired_or_historical"
    duplicate_two = dict(duplicate_one)
    duplicate_two["uid"] = "C-TEST-COMBINE-HERO-DUP2"
    duplicate_two["asset_ref"] = "combine-hero-dup2"
    duplicate_two["bcx"] = 1

    def fulfill_preview(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "account": "player0005",
            "uids": [duplicate_one["uid"], duplicate_two["uid"]],
            "predicted_survivor_uid": duplicate_one["uid"],
            "resulting_bcx": duplicate_one["bcx"] + duplicate_two["bcx"],
            "hazard": {"destroyed_uids": [duplicate_two["uid"]]},
            "buildable": True,
            "observed_at": "2026-09-02T00:00:00Z",
        }))

    serve_idle_cards(page, [duplicate_one, duplicate_two])
    page.route("**/api/combine/preview*", fulfill_preview)
    open_combine_by_print(page, base_url, occupant)
    overlay = page.locator("#combine-dialog-overlay")
    overlay.wait_for(state="visible", timeout=30_000)
    # The preview has loaded once the consequence line states the mocked hazard (the dialog no
    # longer carries a separate `.combine-freshness` note; its observed-at time is in the refusal text).
    page.locator("#combine-dialog-overlay .combine-consequence", has_text="One copy survives").wait_for(timeout=30_000)
    return deed_uid, occupant


def test_rendered_combine_hero_editor_keeps_hero_art_ledger_and_freshness_drops_retired_copy(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 12: replaces `_modal_contract()`'s source-string scan of
    `renderCombineDialogEditor()` (hero art once, no arrow/level/hazard-line/
    subtitle/survivor-line copy, copy rows present, loaded-preview state present,
    destroyed count on the sign button) with a single rendered read of the real
    dialog opened via the board's `?combine=` door against a route-mocked
    idle duplicate + `/api/combine/preview` response.

    Named injection (not applied here; independently watched red/green per
    ): `renderCombineDialogEditor()`'s
    `<div class="combine-editor-left">${heroHtml}${amountHtml}</div>` changed to
    `<div class="combine-editor-left">${amountHtml}</div>` (drops the hero art
    entirely). Expected red: `overlay.locator(".combine-hero-art").count() == 0`
    where this test asserts `== 1`."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid, occupant = _open_combine_dialog(page, base_url)
    overlay = page.locator("#combine-dialog-overlay")

    assert overlay.locator(".combine-hero-art").count() == 1, "hero art must render exactly once"
    assert overlay.locator(".combine-copy-row").count() >= 1, "copy list must render at least one real row"
    consequence = overlay.locator(".combine-consequence")
    assert consequence.count() == 1 and consequence.inner_text() == "One copy survives · 1 copy destroyed", (
        "the loaded preview's consequence line must state the mocked hazard"
    )

    # Retired copy/markup from the old subtitle/hazard-line/survivor-line design
    # must never reappear.
    for retired_class in (
        "combine-hero-arrow", "combine-hero-level", "combine-amount-label",
        "combine-stepper-of", "combine-hazard-line", "combine-hero-sentence",
        "combine-dialog-subtitle", "combine-survivor-line", "combine-destroyed",
        "combine-irreversible",
    ):
        assert overlay.locator(f".{retired_class}").count() == 0, f"retired element .{retired_class} reappeared"

    sign_button = overlay.locator("button.combine-sign")
    sign_button.wait_for(timeout=10_000)
    assert "destroys 1" in sign_button.inner_text().lower(), (
        "sign button must state the real destroyed-copy count from the mocked preview's hazard.destroyed_uids"
    )
    page.close()


def test_retired_combine_copy_is_removed_from_notice_catalog() -> None:
    """Reads `ceres/notices.py`, not `ceres/static/*.js` -- outside this census's
    scope (Python source, legitimately the subject under test), left unchanged."""
    source = NOTICES.read_text(encoding="utf-8")
    assert '"combine_summary"' not in source
    assert '"combine_irreversible"' not in source
