"""Live Chromium coverage for the in-page Ceres slot editor."""

from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Locator, Page

from .conftest import settle, spawn_live_ceres, wait_for_board


SCREEN_DIR = Path(__file__).parents[2] / "work" / "ceres-fr1698-items"
TITLE_DEEDS = ("I-298-6c3970d55b37f5", "I-294-982768c3d66537")



@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    wait_for_board(page)
    return page


def _row_with(page: Page, mode: str) -> int:
    return page.evaluate(
        """mode => [...document.querySelectorAll('#plot-tbody > tr.plot-row')]
 .findIndex(row => {
 const text = row.querySelector('td:nth-child(12)')?.textContent || ''; // Workers ( added Food/hr before it)
 return mode === 'zero' ? /\\b0\\/0\\b/.test(text) : !/\\b0\\/0\\b/.test(text) && /\\/[1-9]\\d*/.test(text);
 })""",
        mode,
    )


def _expand(page: Page, index: int) -> None:
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    if row.locator("xpath=following-sibling::tr[1]").evaluate("node => node.style.display") == "none":
        row.click()
    page.locator("tr.expand-row").nth(index).locator(".slot-editor").wait_for(timeout=30_000)


def _worker_row_index(page: Page) -> int:
    return page.evaluate(
        """() => [...document.querySelectorAll('#plot-tbody > tr.plot-row')]
 .findIndex(row => /\\b[1-9]\\d*\\/[1-9]\\d*(?:\\+R)?\\b/.test(row.children[10]?.textContent || ''))"""
    )


def _mock_completed_refresh(page: Page) -> None:
    page.route(
        "**/api/capture/refresh*",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"started":true}'),
    )
    page.route(
        "**/api/capture/status*",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"state":"done"}'),
    )


def _reload(page: Page) -> None:
    with page.expect_response("**/api/board*") as board_response:
        page.locator("#reload-btn").click()
    assert board_response.value.status == 200


def _detail_url(base_url: str, deed_uid: str) -> str:
    return f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}"


def _board_payload(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_reload_rehydrates_workers_in_an_open_plot(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    index = _worker_row_index(page)
    assert index >= 0
    _expand(page, index)
    expanded = page.locator("tr.expand-row").nth(index)
    worker = expanded.locator(".slot-group[data-group-id='worker']")
    before = worker.locator(".slot-tile[data-occupied='1']").count()
    assert before > 0

    _mock_completed_refresh(page)
    with page.expect_request("**/api/plots/*") as detail_request:
        _reload(page)
    assert detail_request.value.method == "GET"
    page.wait_for_function(
        """index => document.querySelectorAll('tr.expand-row')[index]?.querySelectorAll(
 '.slot-group[data-group-id="worker"] .slot-tile[data-occupied="1"]').length > 0""",
        arg=index,
        timeout=30_000,
    )
    assert expanded.locator(".slot-tile[data-occupied='1']").count() == before
    page.close()


def test_reload_clears_detail_trust_for_a_closed_plot(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    index = _worker_row_index(page)
    assert index >= 0
    _expand(page, index)
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    assert page.locator("tr.expand-row").nth(index).locator(".slot-tile[data-occupied='1']").count() > 0
    row.click()
    page.wait_for_function("index => document.querySelectorAll('tr.expand-row')[index].style.display === 'none'", arg=index)

    _mock_completed_refresh(page)
    _reload(page)
    with page.expect_request("**/api/plots/*") as detail_request:
        row.click()
    assert detail_request.value.method == "GET"
    page.locator("tr.expand-row").nth(index).locator(".slot-editor").wait_for(timeout=30_000)
    assert page.locator("tr.expand-row").nth(index).locator(".slot-tile[data-occupied='1']").count() > 0
    page.close()


def test_worker_name_filter_survives_reload(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    index = _worker_row_index(page)
    assert index >= 0
    _expand(page, index)
    expanded = page.locator("tr.expand-row").nth(index)
    worker_name = expanded.locator(".slot-name").first.inner_text().strip()
    deed_uid = page.locator("#plot-tbody > tr.plot-row").nth(index).get_attribute("data-deed-uid")
    assert worker_name and deed_uid

    _mock_completed_refresh(page)
    with page.expect_request("**/api/plots/*"):
        _reload(page)
    page.wait_for_function(
        """index => document.querySelectorAll('tr.expand-row')[index]?.querySelectorAll(
 '.slot-group[data-group-id="worker"] .slot-tile[data-occupied="1"]').length > 0""",
        arg=index,
        timeout=30_000,
    )
    page.locator("#text-filter").fill(worker_name)
    page.wait_for_function(
        "uid => [...document.querySelectorAll('#plot-tbody > tr.plot-row')].some(row => row.dataset.deedUid === uid)",
        arg=deed_uid,
        timeout=30_000,
    )
    assert page.locator("#plot-tbody > tr.plot-row").count() > 0
    page.close()


def test_stale_detail_response_cannot_merge_after_player_switch(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    board = json.loads(json.dumps(_board_payload(base_url)))
    target = next(row for row in board["plots"] if row["capacity"]["worker_count"] > 0 and row["assets_loaded"])
    deed_uid = target["deed_uid"]
    with urllib.request.urlopen(_detail_url(base_url, deed_uid), timeout=10) as response:
        old_detail = json.loads(response.read().decode("utf-8"))
    switched_board = copy.deepcopy(board)
    switched_board["player"] = "switch-target"
    switched_row = next(row for row in switched_board["plots"] if row["deed_uid"] == deed_uid)
    switched_row["plot"] = "SWITCHED-BOARD"
    switched_detail = copy.deepcopy(old_detail)
    switched_detail["plot"] = "SWITCHED-BOARD"

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.add_init_script(
        """window.__holdPlotDetail = false;
 window.__releasePlotDetail = null;
 window.__plotDetailQueue = [];
 const ceresFetch = window.fetch.bind(window);
 window.fetch = (input, init) => {
 const url = new URL(String(input), location.href);
 const isDetail = url.pathname.startsWith('/api/plots/')
 && !url.pathname.endsWith('/expand')
 && !url.pathname.includes('/candidates')
 && !url.pathname.includes('/removal-impact')
 && !url.pathname.includes('/op-preview');
 if (isDetail && window.__holdPlotDetail) {
 window.__holdPlotDetail = false;
 return new Promise(resolve => {
 window.__releasePlotDetail = body => {
 window.__oldDetailReleased = true;
 resolve(new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}}));
 };
 });
 }
 if (isDetail && window.__plotDetailQueue.length) {
 const body = window.__plotDetailQueue.shift();
 return Promise.resolve(new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}}));
 }
 return ceresFetch(input, init);
 };"""
    )
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    index = page.evaluate(
        "uid => [...document.querySelectorAll('#plot-tbody > tr.plot-row')].findIndex(row => row.dataset.deedUid === uid)",
        deed_uid,
    )
    assert index >= 0
    page.evaluate("() => { window.__holdPlotDetail = true; }")
    page.locator("#plot-tbody > tr.plot-row").nth(index).click()
    page.wait_for_function("typeof window.__releasePlotDetail === 'function'", timeout=30_000)

    def board_route(route) -> None:
        if "player=switch-target" in route.request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(switched_board))
        else:
            route.continue_()

    page.route("**/api/board*", board_route)
    page.evaluate("body => window.__plotDetailQueue.push(body)", switched_detail)
    page.locator("#player-input").fill("switch-target")
    page.locator("#player-input").press("Enter")
    page.wait_for_function(
        "uid => [...document.querySelectorAll('#plot-tbody > tr.plot-row')].find(row => row.dataset.deedUid === uid)?.textContent.includes('SWITCHED-BOARD')",
        arg=deed_uid,
        timeout=30_000,
    )
    page.evaluate("body => window.__releasePlotDetail(body)", old_detail)
    page.wait_for_function("window.__oldDetailReleased === true", timeout=30_000)
    page.wait_for_function(
        "uid => [...document.querySelectorAll('#plot-tbody > tr.plot-row')].find(row => row.dataset.deedUid === uid)?.textContent.includes('SWITCHED-BOARD')",
        arg=deed_uid,
        timeout=30_000,
    )
    assert "SWITCHED-BOARD" in page.locator("#plot-tbody > tr.plot-row").nth(index).inner_text()
    page.close()


def _title_plot(page: Page) -> tuple[int, Page, Locator]:
    rows = page.locator("#plot-tbody > tr.plot-row")
    for deed_uid in TITLE_DEEDS:
        index = rows.evaluate_all(
            "(rows, uid) => rows.findIndex(row => row.dataset.deedUid === uid)", deed_uid
        )
        if index < 0:
            continue
        _expand(page, index)
        expanded = page.locator("tr.expand-row").nth(index)
        title = expanded.locator(".slot-tile[data-group-id='title'][data-occupied='1']")
        if title.count():
            return index, expanded, title.first
    raise AssertionError("live snapshot did not expose a Title-bearing plot")


def test_title_tile_is_actionable_and_does_not_stage(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    _, expanded, title = _title_plot(page)

    assert "is-view-only" not in (title.get_attribute("class") or "")
    assert title.get_attribute("data-actionable") == "1"
    assert title.get_attribute("tabindex") == "0"
    assert "Title op payload shape is unverified; not stageable." not in (title.get_attribute("data-tip") or "")
    assert title.locator(".slot-remove").count() == 1

    title.click()
    expanded.locator(".inline-picker").wait_for(timeout=30_000)
    assert page.locator("#checkout-btn").is_hidden()
    page.close()


def test_clear_plot_includes_actionable_title_and_portfolio_preview_returns_200(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    def removal_route(route) -> None:
        request = route.request.post_data_json
        asset_refs = request["asset_refs"]
        deed_uid = route.request.url.split("/api/plots/", 1)[1].split("/removal-impact", 1)[0]
        payload = {
            "deed_uid": deed_uid,
            "before_powered": True,
            "after_powered": True,
            "removed": [
                {"asset_ref": asset_ref, "own_harvest_pp": 0, "own_construction_pp": None, "own_base_pp": None}
                for asset_ref in asset_refs
            ],
            "plot_residual_delta": 0,
            "plot_ability_delta": 0,
            "harvest_pp_delta": 0,
            "own_construction_pp_delta": {"status": "not_applicable", "value": None, "low": None, "high": None, "reason": "item_pp_not_applicable"},
            "own_base_pp_delta": {"status": "not_applicable", "value": None, "low": None, "high": None, "reason": "item_pp_not_applicable"},
            "notices": [],
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    preview_requests = []

    def preview_route(route) -> None:
        preview_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"account": "player0005", "chunks": [{"operations": []}]}),
        )

    page.route("**/api/plots/*/removal-impact*", removal_route)
    page.route("**/api/op-preview*", preview_route)
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    wait_for_board(page)
    _, expanded, _ = _title_plot(page)

    clear_plot = expanded.locator(".slot-group[data-group-id='worker'] button[data-slot-action='clear_plot']")
    assert clear_plot.is_enabled()
    clear_plot.click()
    page.locator("#checkout-btn").wait_for(timeout=30_000)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    group_ids = panel.locator(".checkout-remove").evaluate_all("buttons => buttons.map(button => button.dataset.groupId)")
    assert {"worker", "totem", "power_core"}.issubset(set(group_ids))
    assert "title" in group_ids

    with page.expect_request("**/api/op-preview*") as preview_request:
        panel.locator("button[data-checkout-action='prepare']").click()
    assert preview_request.value.response().status == 200
    assert len(preview_requests) == 1
    assert "title" in {move["group_id"] for move in preview_requests[0]["moves"]}
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    page.close()


def test_real_board_slot_editor_contract(live_browser: tuple[Browser, str]) -> None:
    """The worker count must exclude bundled item tiles.

 Injection watch: widening the capacity selector to `.slot-grid .slot-tile`
 reproduced the red `8 != 5` count; restoring the `.slot-tile-cell` wrapper
 made the selector assertions green before the unrelated footer assertion at
 line 400 failed.
    """
    page = _page(live_browser)
    powered_index = -1
    ability_index = -1
    replace_index = -1
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        _expand(page, index)
        expanded = page.locator("tr.expand-row").nth(index)
        worker = expanded.locator(".slot-group[data-group-id='worker']")
        count = int(worker.locator(".slot-group-count").inner_text().split("/")[1])
        if powered_index == -1 and count > 0 and worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").count():
            powered_index = index
        if ability_index == -1 and expanded.locator(".slot-tile .slot-flag").count():
            ability_index = index
        if replace_index == -1 and worker.locator(".slot-tile[data-actionable='1']").count():
            replace_index = index
        if powered_index != -1 and ability_index != -1 and replace_index != -1:
            break
    assert powered_index >= 0
    assert ability_index >= 0
    assert replace_index >= 0
    _expand(page, powered_index)

    assert page.locator("#plot-table thead th").count() == 16  # Food/hr; : one table
    assert page.locator(f"#plot-tbody > tr.plot-row").nth(powered_index).locator("+ tr.expand-row td[colspan='16']").count() == 1
    selected_expand = page.locator("tr.expand-row").nth(powered_index)
    worker_group = selected_expand.locator(".slot-group[data-group-id='worker']")
    capacity = int(worker_group.locator(".slot-group-count").inner_text().split("/")[1])
    assert capacity > 0
    assert worker_group.locator(".slot-grid > .slot-tile-cell > .slot-tile").count() == capacity
    assert worker_group.locator(".slot-grid-divider").count() == 1
    assert worker_group.locator(".asset-slot").count() >= 2
    assert selected_expand.locator(".slot-power").count() == 1
    assert page.locator(".slot-hazard-overlay").count() == 1

    empty = worker_group.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    assert empty.count() == 1
    with page.expect_request("**/api/plots/*/candidates*") as candidate_request:
        empty.click()
    assert "group_id=worker" in candidate_request.value.url
    page.locator(".inline-picker").wait_for(timeout=30_000)
    page.locator(".candidate-row").first.wait_for(timeout=30_000)
    assert "candidates" in candidate_request.value.url
    assert "Showing 25 of" in page.locator(".picker-footer").inner_text()

    load_more = page.locator(".inline-picker [data-picker-action='more']")
    if load_more.count():
        before = page.locator(".candidate-row").count()
        with page.expect_request("**/api/plots/*/candidates*"):
            load_more.click()
        page.wait_for_function("count => document.querySelectorAll('.candidate-row').length > count", arg=before)
        assert page.locator(".candidate-row").count() > before

    page.locator(".picker-close").click()
    if replace_index != powered_index:
        _expand(page, replace_index)
    replace_expand = page.locator("tr.expand-row").nth(replace_index)
    occupied = replace_expand.locator(".slot-group[data-group-id='worker'] .slot-tile[data-actionable='1']").first
    occupied.click()
    replace_picker = replace_expand.locator(".inline-picker")
    replace_picker.wait_for(timeout=30_000)
    assert "Replacing" in replace_picker.inner_text()
    assert "Plot change" in replace_picker.inner_text()
    replace_picker.locator(".picker-close").click()

    _expand(page, ability_index)
    ability = page.locator("tr.expand-row").nth(ability_index).locator(".slot-tile[data-actionable='1']").filter(has=page.locator(".slot-flag")).first
    assert ability.locator(".slot-remove").count() == 1
    before_staged = page.locator("tr.expand-row").nth(ability_index).locator(".slot-stage-summary b").inner_text()
    ability.hover()
    ability.locator(".slot-remove").click()
    page.wait_for_function("previous => document.querySelector('tr.expand-row .slot-stage-summary b')?.textContent !== previous", arg=before_staged)
    assert ability.locator(".slot-remove").count() == 0

    zero_index = page.evaluate(
        """deedUid => [...document.querySelectorAll('#plot-tbody > tr.plot-row')]
 .findIndex(row => row.dataset.deedUid === deedUid)""",
        "I-290-10b2d21ed7bb77",
    )
    assert zero_index >= 0
    _expand(page, zero_index)
    zero_worker = page.locator("tr.expand-row").nth(zero_index).locator(".slot-group[data-group-id='worker']")
    assert zero_worker.locator(".slot-group-count").inner_text() == "0/5"
    assert zero_worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").count() == 5
    assert zero_worker.locator(".slot-zero-state").count() == 0
    with page.expect_request("**/api/plots/*/candidates*"):
        zero_worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first.click()
    zero_picker = page.locator("tr.expand-row").nth(zero_index).locator(".inline-picker")
    zero_picker.locator(".candidate-row").first.wait_for(timeout=30_000)
    assert page.locator("tr.expand-row").nth(zero_index).locator('.plot-warning-row[data-plot-warning-kind="power"]').count() == 0

    for width in (1920, 1440, 900, 480):
        page.set_viewport_size({"width": width, "height": 900})
        settle(page)
        assert page.evaluate(
            """width => {
 if (document.documentElement.scrollWidth <= width) return true;
 const widest = [...document.querySelectorAll('body *')]
 .map(node => ({node, rect: node.getBoundingClientRect()}))
 .sort((a, b) => b.rect.right - a.rect.right)[0];
 throw new Error(JSON.stringify({width, documentWidth: document.documentElement.scrollWidth, widest: widest?.node.className, right: widest?.rect.right}));
 }""",
            width,
        )
    page.set_viewport_size({"width": 1920, "height": 1080})
    height = page.locator("tr.expand-row").nth(powered_index).bounding_box()["height"]
    print(f"expanded row rendered height at 1400px: {height:.0f}px")
    assert height > 0
    page.close()


def test_non_contiguous_live_worker_slots_show_addressable_labels(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    found = None
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        _expand(page, index)
        expanded = page.locator("tr.expand-row").nth(index)
        tiles = expanded.locator(".slot-group[data-group-id='worker'] .slot-grid > .slot-tile-cell > .slot-tile")
        occupied = tiles.evaluate_all(
            """tiles => tiles.filter(tile => tile.dataset.occupied === '1').map(tile => Number(tile.dataset.slotIndex) + 1)""",
        )
        if len(occupied) >= 2 and max(occupied) - min(occupied) + 1 != len(occupied):
            found = (index, occupied)
            break
    assert found is not None, "live snapshot did not expose a non-contiguous worker roster"
    index, occupied = found
    expanded = page.locator("tr.expand-row").nth(index)
    empty = expanded.locator(".slot-group[data-group-id='worker'] .slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    slot_number = int(empty.get_attribute("data-slot-index")) + 1
    assert empty.inner_text().strip() == f"+\nAdd Worker {slot_number}"
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / "item-30-non-contiguous-slot-labels.png"), full_page=True)
    print(f"non-contiguous live worker slots: {occupied}; empty label: Add Worker {slot_number}")
    page.close()


def test_preview_requires_explicit_keychain_confirmation(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        """window.__broadcastCalls = [];
 window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
 window.__broadcastCalls.push({account, operations, key});
 callback({success: true, result: {id: 'FAKE-TX-ID'}});
 }};"""
    )
    page.route("**/api/op-verify*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"state":"domain-confirmed"}'))
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    wait_for_board(page)

    powered_index = -1
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        _expand(page, index)
        worker = page.locator("tr.expand-row").nth(index).locator(".slot-group[data-group-id='worker']")
        if int(worker.locator(".slot-group-count").inner_text().split("/")[1]) > 0 and worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").count():
            powered_index = index
            break
    assert powered_index >= 0
    expanded = page.locator("tr.expand-row").nth(powered_index)
    empty = expanded.locator(".slot-group[data-group-id='worker'] .slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with page.expect_request("**/api/plots/*/candidates*"):
        empty.click()
    expanded.locator(".candidate-row").first.wait_for(timeout=30_000)
    expanded.locator(".candidate-row").first.click()
    page.locator("#checkout-btn").wait_for(state="visible", timeout=30_000)
    assert page.locator("#checkout-btn").is_enabled()
    assert page.evaluate("window.__broadcastCalls.length") == 0

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    with page.expect_request("**/api/op-preview*"):
        panel.locator("button[data-checkout-action='prepare']").click()
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    assert "sm_stake_change" in panel.locator(".op-preview-review").text_content()
    assert page.evaluate("window.__broadcastCalls.length") == 0

    panel.locator("button[data-op-action='confirm']").click()
    page.wait_for_function("window.__broadcastCalls.length === 1")
    call = page.evaluate("window.__broadcastCalls[0]")
    assert call["key"] == "Posting"
    assert call["operations"][0][0] == "custom_json"
    # The success surface is the durable banner, not `.op-state`: the confirmed
    # branch re-renders the row synchronously and removes that element in the
    # same tick.
    banner = page.locator("#ceres-confirmation-banner")
    banner.wait_for(timeout=30_000)
    assert "Confirmed in game" in banner.inner_text()
    page.close()

    absent = browser.new_page(viewport={"width": 1400, "height": 900})
    absent.goto(base_url)
    wait_for_board(absent)
    _expand(absent, powered_index)
    empty = absent.locator("tr.expand-row").nth(powered_index).locator(".slot-group[data-group-id='worker'] .slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with absent.expect_request("**/api/plots/*/candidates*"):
        empty.click()
    absent.locator(".candidate-row").first.wait_for(timeout=30_000)
    absent.locator(".candidate-row").first.click()
    absent.locator("#checkout-btn").wait_for(state="visible", timeout=30_000)
    absent.locator("#checkout-btn").click()
    absent_panel = absent.locator("#checkout-panel")
    prepare = absent_panel.locator("button[data-checkout-action='prepare']")
    prepare.wait_for(timeout=30_000)
    assert not prepare.is_disabled()
    assert absent.locator(".slot-keychain-warning").count() == 1
    absent.close()
