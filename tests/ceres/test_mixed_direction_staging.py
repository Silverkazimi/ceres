"""Rendered mixed-direction staging coverage for one deed.

: the mixed-direction test that read the old checkout panel's `.checkout-plot-group` / `.checkout-move-delta` rows was retired; the Changes modal () renders no such elements and the dead CSS went with it.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres, wait_for_board
from . import board_basic_recording as recording


DEED_UID = recording.RUNI.uid


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    return page


def _route_plot_api(page: Page) -> list[str]:
    seen: list[str] = []

    def observe(route) -> None:
        url = route.request.url.split("?", 1)[0]
        if f"/api/plots/{DEED_UID}" in url:
            seen.append(url)
        route.continue_()

    page.route("**/api/plots/**", observe)
    return seen


def _combined_preview(page: Page) -> None:
    def combine(route) -> None:
        response = route.fetch()
        payload = response.json()
        chunks = payload.get("chunks", [])
        operations = [operation for chunk in chunks for operation in chunk.get("operations", [])]
        if len(operations) > 1:
            first = operations[0]
            merged = json.loads(json.dumps(first))
            merged_body = json.loads(merged[1]["json"])
            for operation in operations[1:]:
                body = json.loads(operation[1]["json"])
                for direction in ("stake", "unstake"):
                    for asset_kind in ("cards", "items"):
                        merged_body.setdefault(direction, {}).setdefault(asset_kind, []).extend(
                            body.get(direction, {}).get(asset_kind, [])
                        )
            merged[1]["json"] = json.dumps(merged_body)
            payload["chunks"] = [{**chunks[0], "operations": [merged]}]
            payload["chunk_count"] = 1
            payload["operation_count"] = 1
        route.fulfill(response=response, json=payload)

    page.route("**/api/op-preview*", combine)


def _expand_deed(page: Page):
    row = page.locator(f'tr.plot-row[data-deed-uid="{DEED_UID}"]')
    assert row.count() == 1, f"fixture plot no longer satisfies the precondition: {DEED_UID} is not rendered"
    expand = row.locator("+ tr.expand-row")
    if expand.evaluate("node => node.style.display") == "none":
        row.click()
    expand.locator(".slot-editor").wait_for(timeout=30_000)
    # the band mounts with placeholder frames while the detail is read; tiles are drawn when ready
    expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    return expand


def _worker_tiles(expand: Page):
    worker = expand.locator(".slot-group[data-group-id='worker']")
    empty = worker.locator(".slot-grid .slot-tile.is-empty")
    removable = worker.locator(".slot-tile:has(button.slot-remove)")
    assert empty.count() >= 1 and removable.count() >= 1, (
        f"fixture plot no longer satisfies the precondition: {DEED_UID} needs an open worker slot "
        "and a removable occupied worker tile"
    )
    return worker, empty, removable


def _stage_incoming(page: Page, expand, before: int) -> int:
    worker, empty, _ = _worker_tiles(expand)
    target = empty.first
    slot_index = int(target.get_attribute("data-slot-index"))
    with page.expect_request("**/api/plots/*/candidates*"):
        target.click()
    candidate = expand.locator(".candidate-row").first
    candidate.wait_for(timeout=30_000)
    candidate.click()
    page.wait_for_function(
        f"document.querySelector('#checkout-count')?.textContent === '{before + 1}'",
        timeout=30_000,
    )
    assert worker.locator(f".slot-tile[data-slot-index='{slot_index}']").count() == 1
    return slot_index


def _stage_removal(page: Page, expand, slot_index: int, before: int) -> None:
    tile = expand.locator(f".slot-tile[data-group-id='worker'][data-slot-index='{slot_index}']")
    tile.hover()
    tile.locator("button.slot-remove").click()
    page.wait_for_function(
        f"document.querySelector('#checkout-count')?.textContent === '{before + 1}'",
        timeout=30_000,
    )


def test_mixed_direction_preserves_pending_add_marker(live_browser: tuple[Browser, str]) -> None:
    # Injection: restore the picker choose refusal; without the mixed roster the signed incoming tile cannot render 's pending-add branch.
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script(
        "window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => "
        "callback({success: true, result: {id: 'MIXED-DIRECTION-TEST-TX'}})};"
    )
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"state": "domain-pending", "notices": []}),
        ),
    )
    _combined_preview(page)
    page.goto(base_url)
    wait_for_board(page)
    _route_plot_api(page)
    expand = _expand_deed(page)
    _, _, removable = _worker_tiles(expand)
    removal_slot = int(removable.first.get_attribute("data-slot-index"))
    before = int(page.locator("#checkout-count").text_content() or "0")
    _stage_incoming(page, expand, before)
    _stage_removal(page, expand, removal_slot, before + 1)

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    # No separate Prepare click any more: opening Changes prepares the preview of the staged
    # batch automatically (checkout.js renderCheckoutSurface, "the preview is prepared
    # automatically"), then the footer Sign confirms it.
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # reload only once the signed change is recorded as in flight (the marker the reload restores)
    page.wait_for_function(
        "Object.keys(localStorage).some((key) => key.startsWith('ceres:in-flight:')"
        " && (localStorage.getItem(key) || '').includes('MIXED-DIRECTION-TEST-TX'))",
        timeout=30_000,
    )
    page.reload()
    wait_for_board(page)
    expand = _expand_deed(page)
    expand.locator(".slot-tile.is-pending-add").first.wait_for(timeout=30_000)
    assert expand.locator(".slot-tile.is-pending-add").count() >= 1
    page.close()
