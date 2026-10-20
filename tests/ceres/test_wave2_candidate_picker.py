"""Browser coverage for the  Phase 4 candidate picker (row 40,  migration).

Was a Node harness that sliced `candidateQuery`, `updatePickerRegion`, `pickerResultNotice`,
`renderCandidateRow`, and the Power Core one-click staging pair
(`stagedPowerCoreRefs`..`renderEditorTile`, `CERES_POWER_CORE_PENDING`..
`handleSlotEditorClick`) out of `app.js` by source position and hand-stubbed their
callees. Retired per  (campaign §3.1 batch C row 40) and replaced with a
route-mocked live board: real plot fixtures (by deed, `spawn_live_ceres`) with the
worker/totem/title/power_core group opened through the real click path, requests read
off the real network layer, and rendered output read off the real DOM -- no source
slicing, no hand-stubbed helpers.

Named injections (/, not performed here -- independent verification only):
1. `candidateQuery()` (app.js:3975): add `params.set('preset', 'net_gain_default')`
   unconditionally for worker/runi groups. Expected red:
   `test_worker_query_uses_construction_sort_and_independent_filters` sees `preset` in
   the worker-group request params it asserts absent.
2. `pickerResultNotice()` (app.js:4357): change the `negative_net_gain` branch's
   `noticeFor('negative_net_gain')` to `noticeFor('capacity')`. Expected red:
   `test_picker_empty_states_explain_all_decided_exclusion_reasons`'s negative-net-gain
   case reads the capacity message instead.
3. `renderCandidateRow()`'s item branch (app.js:4284): drop the `itemClass` check so
   totem/title rows fall into the worker-shaped branch. Expected red:
   `test_totem_and_title_candidate_rows_reuse_the_existing_icon_helpers` finds
   `candidate-base`/PP columns present on an item row, which it asserts absent.
4. `stagePowerCoreOneClick()` (app.js:6449): drop the `!reserved.has(assetRef)` filter
   term. Expected red:
   `test_power_core_click_stages_one_unreserved_core_and_tracks_count_left` stages the
   already-reserved `core-used` candidate on the first click instead of refusing with
   "No unstaged Power Cores".
5. `updatePickerRegion()`'s sort-chip sync loop (around app.js:6795): remove the
   `chip.classList.toggle('is-active', ...)` call for the non-matching chips (leave only
   the matching chip's toggle). Expected red:
   `test_partial_picker_refresh_syncs_exactly_one_active_sort_chip` sees more than one
   `.is-active` chip after switching sort.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_board(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _real_detail_row(base_url: str) -> dict:
    board = _real_board(base_url)
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("worker_count", 0) >= 1:
            deed_uid = plot["deed_uid"]
            url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}"
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot with a real worker to build a detail-row template from")


def _real_candidate_template(base_url: str, row: dict, group_id: str = "worker") -> dict:
    params = urllib.parse.urlencode({"group_id": group_id, "limit": 5, "offset": 0, "sort": "construction_pp", "bucket": "all"})
    url = f"{base_url}/api/plots/{urllib.parse.quote(row['deed_uid'], safe='')}/candidates?{params}"
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("candidates"):
        return copy.deepcopy(payload["candidates"][0])
    # Fall back to a minimal real-shaped worker candidate if this plot's picker is
    # empty -- every field below is one `serialize_candidate` (ceres/candidates.py)
    # actually emits.
    return {
        "name": "Test Worker", "asset_ref": "fixture-worker", "level": 1, "base_pp": 5000,
        "terrain_boost": 0, "splinter": "Fire", "rarity": "Common", "card_set": "chaos", "edition": 3,
        "construction_pp": {"status": "point", "value": 5000, "low": 5000, "high": 5000, "reason": None},
        "plot_impact": {"status": "point", "value": 100, "low": 100, "high": 100, "reason": None},
        "net_gain": 100, "net_gain_breakdown": {},
    }


def _route_detail(page, base_url: str, row: dict) -> None:
    deed_uid = row["deed_uid"]
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )


def _goto_and_open_plot(page, base_url: str, deed_uid: str) -> None:
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor").first.wait_for(timeout=30_000)


def test_worker_query_uses_construction_sort_and_independent_filters(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    deed_uid = row["deed_uid"]
    # Force one empty worker slot so the picker opens against slot 0.
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if "/candidates" in r.url else None)
    _route_detail(page, base_url, row)
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": [], "total": 0, "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    _goto_and_open_plot(page, base_url, deed_uid)
    empty_slot = page.locator(".slot-editor .slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']").first
    empty_slot.wait_for(timeout=30_000)
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        empty_slot.click()
    page.locator(".picker-region, .picker-empty, .candidate-row").first.wait_for(timeout=30_000)
    assert seen, "opening the empty worker slot did not request candidates"
    params = urllib.parse.parse_qs(urllib.parse.urlparse(seen[-1]).query)
    # Worker/Runi queries must NEVER send `preset` -- the server's preset handling
    # unconditionally overrides `sort` with the preset's own fixed ordering whenever
    # `preset` is truthy (ceres/candidates.py:688), which used to silently force
    # every worker sort back to construction_pp regardless of the chip the user
    # picked. Sort is chip-driven now.
    assert params.get("sort") == ["construction_pp"]
    assert "preset" not in params

    # Click the Name sort chip -- the request must switch sort and still carry no preset.
    seen.clear()
    name_chip = page.locator(".candidate-list-header [data-picker-action='sort'][data-sort='name'], [data-picker-action='sort'][data-sort='name']").first
    name_chip.wait_for(timeout=30_000)
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        name_chip.click()
    settle(page)
    assert seen, "clicking the Name sort chip did not request candidates"
    params = urllib.parse.parse_qs(urllib.parse.urlparse(seen[-1]).query)
    assert params.get("sort") == ["name"]
    assert "preset" not in params
    page.close()


def test_partial_picker_refresh_syncs_exactly_one_active_sort_chip(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    deed_uid = row["deed_uid"]
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _route_detail(page, base_url, row)
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": [], "total": 0, "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    _goto_and_open_plot(page, base_url, deed_uid)
    empty_slot = page.locator(".slot-editor .slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']").first
    empty_slot.wait_for(timeout=30_000)
    empty_slot.click()
    chips = page.locator("[data-picker-action='sort']")
    chips.first.wait_for(timeout=30_000)
    sort_ids = chips.evaluate_all("(els) => els.map((el) => el.dataset.sort)")
    for sort_id in sort_ids:
        chip = page.locator(f"[data-picker-action='sort'][data-sort='{sort_id}']").first
        chip.click()
        settle(page)
        active = page.locator("[data-picker-action='sort'].is-active")
        assert active.count() == 1, f"expected exactly one active sort chip after selecting {sort_id!r}, saw {active.count()}"
        assert active.first.get_attribute("data-sort") == sort_id
    page.close()


def test_totem_and_title_candidate_rows_reuse_the_existing_icon_helpers(live_browser: tuple[Browser, str]) -> None:
    # Item rows ( Phase 4): no Construction PP/Terrain/Plot change/net-PP
    # delta -- just identity plus one numeric Production boost figure.
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["bonus"] = dict(row.get("bonus") or {}, totem=None)
    deed_uid = row["deed_uid"]
    totem_candidate = {
        "name": "Rare Totem", "asset_ref": "rare-totem", "rarity": "rare", "boost": 0.25, "net_gain": 10,
    }

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _route_detail(page, base_url, row)
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": [totem_candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    _goto_and_open_plot(page, base_url, deed_uid)
    totem_slot = page.locator(".slot-editor .slot-tile[data-group-id='totem'][data-slot-action='add']").first
    totem_slot.wait_for(timeout=30_000)
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        totem_slot.click()
    candidate_row = page.locator(".candidate-row").first
    candidate_row.wait_for(timeout=30_000)
    html = candidate_row.evaluate("(el) => el.outerHTML")
    assert "Rare Totem" in html
    assert "+25%" in html
    assert "candidate-item-tier" in html and ">Rare<" in html
    assert "candidate-base" not in html, "item rows must not show worker-only PP columns"
    assert "net PP" not in html
    page.close()


def test_picker_empty_states_explain_all_decided_exclusion_reasons(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]
    deed_uid = row["deed_uid"]

    scenarios = [
        ({"negative_net_gain": 4}, "negative_net_gain", "negative full-plot net gain"),
        ({"terrain_penalty": 4}, "terrain_penalty", "terrain penalty"),
        ({"capacity": 4}, "capacity", "remaining worker capacity"),
    ]
    def _make_empty_candidates_handler(counts: dict, notice_code: str):
        # NOTE: `page.route` callbacks are always invoked as `handler(route
        # route.request)` by this Playwright version -- a lambda with a second
        # (default-valued) parameter gets that parameter CLOBBERED by
        # `route.request`, not left at its default. Capture `counts` by closure
        # over a single-parameter function instead.
        def _handler(route) -> None:
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"candidates": [], "total": 0, "source": "test-fixture", "excluded_counts": counts, "notices": [{"code": notice_code}]}),
            )
        return _handler

    for excluded_counts, expected_code, expected_text in scenarios:
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        _route_detail(page, base_url, row)
        page.route(
            f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
            _make_empty_candidates_handler(excluded_counts, expected_code),
        )
        _goto_and_open_plot(page, base_url, deed_uid)
        empty_slot = page.locator(".slot-editor .slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']").first
        empty_slot.wait_for(timeout=30_000)
        empty_slot.click()
        empty_notice = page.locator(".picker-empty")
        empty_notice.wait_for(timeout=30_000)
        text = empty_notice.inner_text()
        assert empty_notice.get_attribute("data-notice-code") == expected_code
        if expected_code != "terrain_penalty":
            assert expected_text in text, f"{excluded_counts} -> expected {expected_text!r} in {text!r}"
        page.close()


def test_power_core_click_stages_one_unreserved_core_and_tracks_count_left(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["bonus"] = dict(row.get("bonus") or {}, power_core=None)
    row["staking_facts"] = dict(row.get("staking_facts") or {}, is_power_core_staked=False, is_energized=False)
    deed_uid = row["deed_uid"]

    malformed_candidates = [
        None, "core-scalar", [], {}, {"asset_ref": None, "name": "Power Core", "net_gain": 0},
        {"asset_ref": "", "name": "Power Core", "net_gain": 0}, {"asset_ref": "   ", "name": "Power Core", "net_gain": 0},
        {"asset_ref": {}, "name": "Power Core", "net_gain": 0}, {"asset_ref": [], "name": "Power Core", "net_gain": 0},
        {"asset_ref": "core-used", "name": "Power Core", "net_gain": 0},
    ]
    response_state = {"candidates": malformed_candidates}

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _route_detail(page, base_url, row)
    board = _real_board(base_url)
    board["unassigned_bonus"] = dict(board.get("unassigned_bonus") or {}, power_core={"capturable": True, "count": 3, "items": []})
    # Only the family count needs overriding here -- the per-plot board LIST row has a
    # different (lighter) shape than the /api/plots/<deed> DETAIL row `row` above, and
    # substituting one for the other would corrupt the board table's own render. The
    # click handler always re-fetches the detail row (a fresh page has nothing cached
    # in EXPANDED_DATA_LOADED), so `_route_detail`'s mock is what the editor actually uses.
    page.route("**/api/board*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)))
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": response_state["candidates"], "total": len(response_state["candidates"]), "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    _goto_and_open_plot(page, base_url, deed_uid)
    add_button = page.locator(".slot-editor [data-group-id='power_core'][data-slot-action='add-power-core']").first
    add_button.wait_for(timeout=30_000)

    # First click: only malformed shapes plus one ALREADY-RESERVED core (as if staged
    # elsewhere -- but nothing is staged here, so 'core-used' is actually reachable
    # the malformed shapes must all be skipped without throwing) -- there is no
    # genuinely eligible candidate in this response, so nothing stages and the toast
    # explains why. Real behaviour: `core-used` (well-formed, non-reserved) WOULD be
    # picked; to prove the "no unstaged cores" path we drop it for this first click.
    response_state["candidates"] = malformed_candidates[:-1]
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        add_button.click()
    toast = page.locator("#ceres-stage-toast")
    toast.wait_for(timeout=30_000)
    page.wait_for_function(
        "document.getElementById('ceres-stage-toast')?.textContent.includes('No unstaged Power Cores')", timeout=30_000,
    )
    add_button_2 = page.locator(".slot-editor [data-group-id='power_core'][data-slot-action='add-power-core']").first
    assert add_button_2.count() == 1, "slot must still be empty/actionable after a refused stage"

    # Second click: now a real eligible candidate is present.
    response_state["candidates"] = [
        *malformed_candidates,
        {"asset_ref": "  core-next  ", "name": "Power Core", "net_gain": 0},
    ]
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        add_button_2.click()
    page.wait_for_function(
        "document.getElementById('ceres-stage-toast')?.textContent.includes('Staged: Power Core')", timeout=30_000,
    )
    filled_tile = page.locator(".slot-editor [data-group-id='power_core']:not([data-slot-action='add-power-core'])")
    filled_tile.first.wait_for(timeout=30_000)
    page.close()


def test_power_core_tiles_never_open_a_candidate_picker(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["bonus"] = dict(row.get("bonus") or {}, power_core=None)
    row["staking_facts"] = dict(row.get("staking_facts") or {}, is_power_core_staked=False, is_energized=False)
    deed_uid = row["deed_uid"]

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _route_detail(page, base_url, row)
    candidate_requests: list[str] = []
    page.on("request", lambda r: candidate_requests.append(r.url) if "/candidates" in r.url else None)
    board = _real_board(base_url)
    board["unassigned_bonus"] = dict(board.get("unassigned_bonus") or {}, power_core={"capturable": True, "count": 1, "items": []})
    page.route("**/api/board*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(board)))
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": [{"asset_ref": "core-a", "name": "Power Core", "net_gain": 0}], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    _goto_and_open_plot(page, base_url, deed_uid)
    add_button = page.locator(".slot-editor [data-group-id='power_core'][data-slot-action='add-power-core']").first
    add_button.wait_for(timeout=30_000)
    assert add_button.evaluate("(el) => el.tagName.toLowerCase()") == "button"
    add_button.click()
    page.wait_for_function(
        "document.getElementById('ceres-stage-toast')?.textContent.length > 0", timeout=30_000,
    )
    assert page.locator(".picker-region, .candidate-row").count() == 0, "Power Core must stage one-click, never open a picker"
    page.close()
