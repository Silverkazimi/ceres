""": a confirmed feed refreshes the harvest box in the still-open plot."""

from __future__ import annotations

import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Browser, TimeoutError as PlaywrightTimeoutError

from .conftest import spawn_live_ceres


def _completion_row(row: dict) -> dict:
    result = copy.deepcopy(row)
    # 's `assets_loaded` gate (rows.js::loadPlotDetail): a row without it renders the
    # "workers did not load" error state instead of the editor body -- the harvest bar never
    # mounts and the click path this guard drives never gets off the ground.
    result["assets_loaded"] = True
    result["is_construction"] = True
    result["worksite_type"] = "Grain Farm"
    result["worksite_completion"] = {
        "kind": "worksite_completion",
        "project_id": "ir8310-project",
        "to": "grain",
        "to_label": "Grain Farm",
        "grain_cost": 1,
    }
    result["harvest"] = {
        "state": "construction",
        "construction": {
            "to": "grain",
            "to_label": "Grain Farm",
            "started_at": "2026-09-20T00:00:00Z",
            "ready_at": "2026-09-25T00:00:00Z",
            "progress_pct": 100,
        },
    }
    return result


def _harvest_payload(uid: str, *, fresh: bool) -> dict:
    projection = (
        {
            "state": "harvesting",
            "source": "fresh",
            "token": "GRAIN",
            "worksite_type": "Grain Farm",
            "anchor_at": "2026-09-23T00:00:00Z",
            "net_at_anchor": 1,
            "gross_per_hour": 1,
            "hours_at_anchor": 0,
            "tax_rate": 0,
            "costs": [],
        }
        if fresh
        else {
            "state": "construction",
            "source": "fresh",
            "construction": _completion_row({"deed_uid": uid})["harvest"]["construction"],
            "worksite_completion": _completion_row({"deed_uid": uid})["worksite_completion"],
        }
    )
    return {
        "server_now": "2026-09-23T00:00:00Z",
        "projection": projection,
        "region_balances": None,
        "affordability": {"status": "unknown"},
        "issues": [],
        "signing_enabled": True,
    }


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def test_confirmed_feed_rerenders_open_harvest_box_from_fresh_projection(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.add_init_script(
        "window.__ir8310KeychainCalls = 0; window.hive_keychain = {requestBroadcast(_account, _ops, _key, callback) "
        "{ window.__ir8310KeychainCalls += 1; callback({success: true, result: {id: 'ir8310-mocked-tx'}, data: {key: 'Posting'}}); }};"
    )
    selected: dict[str, dict] = {}
    updated: dict[str, dict] = {}
    fresh_reads: list[str] = []
    initial_reads: list[str] = []
    fed = {"value": False}

    def board_route(route) -> None:
        payload = route.fetch().json()
        assert payload.get("plots"), "the local board fixture must provide a plot"
        row = next(plot for plot in payload["plots"] if plot.get("deed_uid"))
        ready = _completion_row(row)
        selected["row"] = ready
        updated_row = copy.deepcopy(ready)
        updated_row["is_construction"] = False
        updated_row["worksite_completion"] = None
        updated_row["harvest"] = {"state": "harvesting", "token": "GRAIN"}
        updated["row"] = updated_row
        payload["plots"][payload["plots"].index(row)] = ready
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def plot_route(route) -> None:
        url = route.request.url
        uid = selected.get("row", {}).get("deed_uid")
        if not uid or f"/api/plots/{uid}" not in url:
            route.continue_()
            return
        if "/harvest" in url:
            is_fresh = "fresh=1" in url
            if is_fresh:
                fresh_reads.append(url)
            else:
                initial_reads.append(url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_harvest_payload(uid, fresh=is_fresh)))
            return
        # `fed`, not the URL shape: board.js chooses GET (not POST .../expand) for a row whose
        # board snapshot already carries `assets_loaded: true` (real for this fixture row), so
        # the very same GET endpoint serves both the plot's first open and the post-confirm
        # refresh -- only the domain-confirmed feed (verify_route, below) tells them apart.
        route.fulfill(status=200, content_type="application/json", body=json.dumps(updated["row"] if fed["value"] else selected["row"]))

    def preview_route(route) -> None:
        uid = selected["row"]["deed_uid"]
        operation = ["custom_json", {
            "id": "sm_land_operation",
            "json": json.dumps({"op": "update_worksite", "deed_uid": uid}),
            "required_auths": [],
            "required_posting_auths": ["player0005"],
        }]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "account": "player0005", "chunks": [{"operations": [operation]}],
        }))

    def verify_route(route) -> None:
        fed["value"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "state": "domain-confirmed", "refreshed_deeds": [selected["row"]["deed_uid"]],
        }))

    def inclusion_route(route) -> None:
        route.fulfill(status=200, content_type="application/json", body='{"included":true,"block_num":123}')

    page.route("**/api/board*", board_route)
    page.route("**/api/plots/**", plot_route)
    page.route("**/worksite-preview*", preview_route)
    page.route("**/api/op-inclusion*", inclusion_route)
    page.route("**/api/op-verify*", verify_route)
    page.goto(f"{base_url}/?player=sample01", timeout=8_000)

    try:
        page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=15_000)
    except PlaywrightTimeoutError:
        pytest.fail("live Ceres board did not render a plot row within 15 seconds", pytrace=False)

    uid = selected["row"]["deed_uid"]
    row = page.locator(f'tr.plot-row[data-deed-uid="{uid}"]')
    expanded = page.locator(f'tr.expand-row[data-deed-uid="{uid}"]')
    # The harvest box only exists inside the OPEN single-plot view (ceres/static/editor.js's
    # renderEditorBandContents calls harvestBarHtml, never the collapsed board row) -- so the
    # plot's initial (non-forced) harvest read fires on opening the row, not on board load.
    try:
        with page.expect_response(
            lambda response: (
                urlsplit(response.url).path.endswith(f"/api/plots/{uid}/harvest")
                and "fresh" not in parse_qs(urlsplit(response.url).query)
            ),
            timeout=8_000,
        ):
            row.click(timeout=8_000)
    except PlaywrightTimeoutError:
        pytest.fail("opening the plot did not issue its initial harvest read within 8 seconds", pytrace=False)
    try:
        expanded.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        pytest.fail("selected plot did not open its detail row", pytrace=False)
    bar = page.locator(f'.harvest-bar[data-deed-uid="{uid}"]')
    feed = bar.locator('[data-harvest-action="feed-workers"]')
    try:
        feed.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        pytest.fail("open plot did not show its ready Feed workers action", pytrace=False)
    assert len(initial_reads) == 1, f"expected the pre-feed cached projection, got {initial_reads}"
    feed.click(timeout=8_000)
    sign = page.locator('#worksite-modal-overlay [data-worksite-action="broadcast"]:not([disabled])')
    try:
        sign.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        pytest.fail("Feed workers did not open the mocked confirmation modal", pytrace=False)
    sign.click(timeout=8_000)

    verdict = page.locator("#ceres-confirmation-banner .worksite-banner-title")
    try:
        verdict.wait_for(state="visible", timeout=8_000)
        page.wait_for_function(
            "() => document.querySelector('#ceres-confirmation-banner .worksite-banner-title')?.innerText.includes('Workers fed — worksite complete')",
            timeout=8_000,
        )
    except PlaywrightTimeoutError:
        pytest.fail("mocked domain confirmation did not finish the Feed workers action", pytrace=False)
    assert initial_reads, "the stale construction projection was not cached before the feed"
    assert page.evaluate("window.__ir8310KeychainCalls") == 1, "feed did not use the single mocked Keychain request"
    # These three assert on the OPEN plot's harvest box itself -- the maintainer's reported
    # symptom (: closing and reopening the plot shows it correctly, so the bug is
    # the box not repainting off the fresh projection, not a broadcast/verification failure).
    assert not feed.is_visible(), "confirmed feed left the stale Feed workers control in the open plot"
    assert bar.locator('[data-harvest-action="open-plot"]').is_visible(), "confirmed feed did not show the active harvest action"
    assert "You receive" in bar.inner_text(), "confirmed feed left the open harvest box in its stale build state"
    assert len(fresh_reads) == 1, f"expected one forced harvest read after the feed, got {fresh_reads}"
    assert expanded.is_visible(), "the plot detail closed during the feed refresh"
    assert page.locator("#worksite-modal-overlay").is_hidden()
    page.close()
