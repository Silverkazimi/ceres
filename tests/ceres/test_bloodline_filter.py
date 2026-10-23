from __future__ import annotations

import copy
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest
from playwright.sync_api import Browser

from ceres.candidates import rank_candidates
from .conftest import spawn_live_ceres


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)

DETAILS = {
    866: {
        "id": 866, "name": "Bloodline Holder", "color": "Green", "sub_type": "Elf",
        "stats": {"land_abilities": [[[]] for _ in range(9)] + [[ ["BLOODLINE", 0.3, "Elf"] ]]},
    },
    40001: {"id": 40001, "name": "Elf Companion", "color": "Red", "sub_type": "Elf"},
    40002: {"id": 40002, "name": "Human Companion", "color": "Blue", "sub_type": "Human"},
    867: {
        "id": 867, "name": "Incoming Holder", "color": "Green", "sub_type": "Elf",
        "stats": {"land_abilities": [[["BLOODLINE", 0.3, "Elf"]]]},
    },
}


def _report() -> dict:
    facts = {
        "max_workers_allowed": 2, "total_base_pp_cap": 1000000,
        "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False,
        "deed_rarity_boost": 0, "totem_boost": 0, "title_boost": 0, "runi_boost": 0,
        "deed_status_token_boost": 0, "red_biome_modifier": 0, "blue_biome_modifier": 0,
        "white_biome_modifier": 0, "black_biome_modifier": 0, "green_biome_modifier": 0,
        "gold_biome_modifier": 0,
    }
    return {
        "plots": [{"deed_uid": "plot-bloodline", "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"}, "staking_facts": facts}],
        "workers": [{"name": "Solo Holder", "card_detail_id": 866, "land_base_pp": 150, "total_harvest_pp": 150, "asset_ref": "holder", "stake_ref_uid": "plot-bloodline", "asset_kind": "worker_card", "slot": 1}],
        "cards": [
            {"asset_ref": "elf", "uid": "elf", "classification": {"bucket": "unstaked_apparent"}, "facts": {"uid": "elf", "player": "player0005", "card_detail_id": 40001, "land_base_pp": 8000, "level": 1}},
            {"asset_ref": "human", "uid": "human", "classification": {"bucket": "unstaked_apparent"}, "facts": {"uid": "human", "player": "player0005", "card_detail_id": 40002, "land_base_pp": 9000, "level": 1}},
            {"asset_ref": "holder", "facts": {"level": 10}},
        ],
        "unassigned_items": {},
    }


def test_pair_lens_filters_real_companion_by_sub_type_without_scoring_priority():
    body = rank_candidates(
        _report(), "plot-bloodline", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=10, details_by_id=DETAILS,
        bloodline_sub_types={"Elf"},
    )
    assert [candidate["asset_ref"] for candidate in body["candidates"]] == ["elf"]
    assert body["candidates"][0]["sub_type"] == "Elf"


def test_rank_candidate_holder_carries_level_correct_bloodline_metadata():
    report = _report()
    report["cards"].append({
        "asset_ref": "incoming-holder", "uid": "incoming-holder",
        "classification": {"bucket": "unstaked_apparent"},
        "facts": {"uid": "incoming-holder", "player": "player0005", "card_detail_id": 867, "land_base_pp": 8000, "level": 1},
    })
    body = rank_candidates(
        report, "plot-bloodline", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=10, details_by_id=DETAILS,
    )
    candidate = next(item for item in body["candidates"] if item["asset_ref"] == "incoming-holder")
    assert candidate["land_abilities"] == [["BLOODLINE", 0.3, "Elf"]]
    assert candidate["sole_holder_for"] == ["BLOODLINE"]


def test_solo_holder_slot_is_a_normal_eviction_target_while_pair_lens_is_available():
    # 2026-08-26 ruling (supersedes the earlier hard-refusal ruling): a solo
    # BLOODLINE holder's slot is no longer refused -- it scores normally against
    # incoming candidates, same as any other occupied slot.
    report = _report()
    report["plots"][0]["staking_facts"]["max_workers_allowed"] = 1
    body = rank_candidates(
        report, "plot-bloodline", "worker", player="player0005", now=NOW,
        bucket="idle", include_cooling=True, limit=10, details_by_id=DETAILS, slot=0,
    )
    assert body["candidates"]
    assert body["candidates"][0]["evicted"]["name"] == "Solo Holder"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_detail_row(base_url: str) -> dict:
    """A REAL `GET /api/plots/{deed}` row for a plot with >=1 real worker, used
    only as a field-shape template -- its `workers` list is overwritten by callers."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("worker_count", 0) >= 1:
            deed_uid = plot["deed_uid"]
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot with a real worker to build a detail-row template from")


def _real_candidate_template(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if (plot.get("capacity") or {}).get("open_slots", 0) > 0:
            deed_uid = plot["deed_uid"]
            params = urllib.parse.urlencode({"group_id": "worker", "limit": 5, "offset": 0, "sort": "construction_pp", "bucket": "all", "preset": "net_gain_default"})
            url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}"
            with urllib.request.urlopen(url, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("candidates"):
                return copy.deepcopy(payload["candidates"][0])
    raise AssertionError("no live plot returned a worker candidate to build a fixture template from")


def test_bloodline_picker_reaches_server_with_sub_type_and_staged_metadata_survives(live_browser: tuple[Browser, str]) -> None:
    r"""Replaces the node-sliced picker-lens/mutate test (census row 5's node half).
    Real REST fixture: a real plot detail row, its worker list replaced by ONE real
    worker object annotated as a solo BLOODLINE Elf holder. Clicking the holder's
    real SOLO badge (`bloodlineCardBadgeHtml()`, `data-notice-action="open-bloodline-
    pairs"`) drives the exact same `openPicker(..., {bloodline_pairs: true})` call
    site the picker-lens test used to slice out and exercise in isolation.

    Discovered while writing this test (not in the census's description): the SOLO
    badge lives INSIDE the holder's own tile, so `openBloodlinePairPicker()`'s
    `noticeAction.closest('[data-slot-index]')` resolves to the HOLDER'S OWN slot,
    and since that slot is occupied, `openPicker()` opens in `'replace'` mode on
    the holder's own tile -- "find a matching companion" replaces the holder, it
    does not fill a separate empty slot. So "staged metadata survives a mutate" is
    checked here as: the picked replacement (itself carrying a BLOODLINE Elf entry,
    i.e. a real alternate holder of the same sub_type -- `candidates.py:756`'s
    `role: replacement_holder` case) renders its OWN `.slot-bloodline-badge.is-solo`
    badge after staging, proving `mutateEditorSlot()` carried the incoming
    candidate's `land_abilities` into `row.workers` and `annotateEditorBloodlines()`
    recomputed the new tenant's status, rather than the tile silently keeping the
    OLD holder's stale annotation.

    Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `mutateEditorSlot()`, the worker-stage line
    `land_abilities: occupant.land_abilities || []` was changed to
    `land_abilities: []` (drops the incoming candidate's own BLOODLINE entry on
    stage). Red: `playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout
    30000ms exceeded ... waiting for locator(".slot-group[data-group-id='worker']
    .slot-bloodline-badge.is-solo").first to be visible` -- the newly staged tile
    carried no BLOODLINE entry at all to annotate. Restoring the line made this
    test pass again (whole-file run: 5 passed)."""
    browser, base_url = live_browser
    row = _real_detail_row(base_url)
    deed_uid = row["deed_uid"]
    holder = copy.deepcopy(row["workers"][0])
    holder["slot"] = 1
    holder["land_abilities"] = [["BLOODLINE", 0.3, "Elf"]]
    holder["sole_holder_for"] = []
    row["workers"] = [holder]
    row["capacity"] = dict(row["capacity"], potential_card_capacity=1, open_slots=0)

    candidate = _real_candidate_template(base_url)
    candidate["sub_type"] = "Elf"
    candidate["land_abilities"] = [["BLOODLINE", 0.3, "Elf"]]

    seen_requests: list[str] = []

    def fulfill_candidates(route) -> None:
        seen_requests.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        re.compile(rf"/api/plots/{re.escape(urllib.parse.quote(deed_uid, safe=''))}(/expand)?(\?|$)"),  # GET detail or POST expand
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill_candidates)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    badge = page.locator(".slot-editor .slot-bloodline-badge.is-solo").first
    badge.wait_for(timeout=30_000)
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        badge.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)

    assert seen_requests, "clicking the SOLO badge did not open a picker that fetched candidates"
    request_url = urllib.parse.urlparse(seen_requests[0])
    params = urllib.parse.parse_qs(request_url.query)
    assert params.get("bloodline_sub_type") == ["Elf"], f"picker did not filter by the holder's Bloodline sub_type: {params}"

    page.locator(".candidate-row").first.click()
    new_badge = page.locator(".slot-group[data-group-id='worker'] .slot-bloodline-badge.is-solo").first
    new_badge.wait_for(timeout=30_000)
    assert candidate["name"] in page.locator(".slot-group[data-group-id='worker'] .slot-name").first.inner_text()
    page.close()


def test_bloodline_pair_break_removal_notice_reopens_a_working_companion_picker(live_browser: tuple[Browser, str]) -> None:
    r""" -- Replaces the two node-sliced `bloodlinePlotSubTypes()`/
    `pickerResultNotice()` tests (census row 5) with the ONE real click path that
    reaches `openBloodlinePairPicker()` for a plot that WAS an active pair:
    removing the companion from a paired Bloodline holder (via the tile's real
    remove button) triggers the server's `bloodline_pair_deactivated` removal-impact
    notice with its "Find a matching companion" action button
    (`ceres/candidates.py:748-759`) -- clicking it must reopen a picker that still
    finds the holder's Bloodline sub_type, not the "no Bloodline holder on this
    plot" refusal.

    SCOPE NOTE (discrepancy with the census's literal description, reported per
    ticket instruction rather than forced): there is no real UI action anywhere in
    the shipped product that opens the `bloodline_pairs` picker while a pair is
    still ACTIVE and un-touched -- `bloodlineCardBadgeHtml()` renders the SOLO
    holder's badge as the only clickable trigger (`is-solo`; the `is-paired` badge
    is a plain non-interactive `<span>`), and the picker's own filter panel has no
    `bloodline_pairs` checkbox (only `q`/`rarity`/`splinter`/`edition`/`min_pp`/
    `bucket`/`ready_now`/`include_terrain_penalised` are wired to
    `[data-picker-filter]`). The ONLY other entry point is this removal-impact
    notice, and by the time its button is clickable, `mutateEditorSlot()` has
    already removed the companion from `row.workers` client-side (`stagePricedRemovals`,
    app.js:6270-6276) -- so `bloodlinePlotSubTypes(row)` runs on an ALREADY-broken
    pair at click time regardless of old or new code, which is why this test's
    injection (below) targets a different, but still real and load-bearing, line:
    the notice's own action-button rendering, not `bloodlinePlotSubTypes()` itself.
    Converting the census's literal "still-paired-at-call-time" scenario is not
    possible without either an `app.js` export (forbidden by ) or
    duplicating picker logic in the test (forbidden by the campaign method) --
    flagged here rather than forced.

    Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s notice-action button template, `notice.action?.id ===
    'open_bloodline_pairs'` was changed to `notice.action?.id ===
    'nonexistent_action_id'` (the action button is never rendered for any real
    notice). Red: `playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout
    30000ms exceeded ... waiting for locator(".ceres-notice .notice-action") to be
    visible`. Restoring the line made this test pass again (whole-file run:
    5 passed)."""
    browser, base_url = live_browser
    row = _real_detail_row(base_url)
    deed_uid = row["deed_uid"]
    holder = copy.deepcopy(row["workers"][0])
    holder["slot"] = 1
    holder["asset_ref"] = "holder-asset"
    holder["land_abilities"] = [["BLOODLINE", 0.3, "Elf"]]
    holder["sub_type"] = "Elf"
    companion = copy.deepcopy(row["workers"][0])
    companion["slot"] = 2
    companion["asset_ref"] = "companion-asset"
    companion["land_abilities"] = []
    companion["sub_type"] = "Elf"
    row["workers"] = [holder, companion]
    row["capacity"] = dict(row["capacity"], potential_card_capacity=2, open_slots=0)

    removal_impact_response = {
        "deed_uid": deed_uid, "before_powered": True, "after_powered": True,
        "removed": [{"asset_ref": companion["asset_ref"], "name": companion["name"], "own_harvest_pp": 0.0, "own_construction_pp": 0.0, "own_base_pp": 0.0}],
        "plot_residual_delta": 0.0, "plot_ability_delta": 0.0, "harvest_pp_delta": 0.0,
        "own_construction_pp_delta": {"status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None},
        "own_base_pp_delta": {"status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None},
        "notices": [{
            "code": "bloodline_pair_deactivated", "kind": "warning", "severity": "caution", "blocking": "none",
            "message": "Removing this card breaks its active Bloodline pairing.", "reason": "the active pair is broken by this removal",
            "evidence": {}, "context": {"deed_uid": deed_uid, "bloodline": "Toil and Kin", "sub_type": "Elf", "pp_delta": 0},
            "action": {"id": "open_bloodline_pairs", "label": "Find a matching Elf companion", "role": "companion_for_holder", "sub_type": "Elf"},
        }],
    }
    empty_candidates = {"candidates": [], "total": 0, "source": "test-fixture", "excluded_counts": {}, "notices": []}
    candidate_requests: list[str] = []

    def fulfill_candidates(route) -> None:
        candidate_requests.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(empty_candidates))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        re.compile(rf"/api/plots/{re.escape(urllib.parse.quote(deed_uid, safe=''))}(/expand)?(\?|$)"),  # GET detail or POST expand
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/removal-impact*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(removal_impact_response),
    ))
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill_candidates)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    companion_tile = page.locator(".slot-group[data-group-id='worker'] .slot-tile-cell:nth-child(2) .slot-tile")
    companion_tile.wait_for(timeout=30_000)
    companion_tile.hover()
    companion_tile.locator(".slot-remove").click(force=True)
    notice_button = page.locator(".ceres-notice .notice-action")
    notice_button.wait_for(timeout=30_000)
    assert "companion" in notice_button.inner_text().lower()
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        notice_button.click()
    picker_region = page.locator(".picker-region, .picker-empty, .candidate-row").first
    picker_region.wait_for(timeout=30_000)
    empty_notice = page.locator(".picker-empty")
    empty_notice.wait_for(timeout=30_000)
    assert "No Bloodline holder on this plot" not in empty_notice.inner_text(), (
        "the holder's Bloodline sub_type was lost -- picker fell back to the refusal message"
    )
    assert candidate_requests, "the notice action did not reopen a picker that queried candidates"
    request_params = urllib.parse.parse_qs(urllib.parse.urlparse(candidate_requests[-1]).query)
    assert request_params.get("bloodline_sub_type") == ["Elf"]
    page.close()
