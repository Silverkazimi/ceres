from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request
import pytest
from playwright.sync_api import Browser

from ceres.capacity import (
    WORKSITE_WORKER_CAPACITY,
    potential_worker_capacity,
    powered_worker_capacity,
    powered_worker_slots,
    structure_capacity_index,
)
from ceres.candidates import _projected_worksite_row, _worker_slot_capacity
from .conftest import spawn_live_ceres

# an earlier queue's source-text guard here (kept, NOT
# converted, pending an maintainer ruling on the dead `powerWarning` computation
# see former test_power_warning_is_informative_and_never_disables_worker_tiles)
# has been resolved by this queue's dispatcher ruling: see
# test_worker_power_warning_computation_is_dead_code_not_rendered_anywhere
# below, which cites the dead computation by line number in the running
# source instead of keeping an unread `APP_JS` constant here.


def _worksite(*, capacity=0, core=False, energized=False, runi=False):
    return {
        "facts": {"worksite_type": "Grain Farm"},
        "worksite_facts": {"project_type": "worksite_grain"},
        "staking_facts": {
            "max_workers_allowed": capacity,
            "is_power_core_staked": core,
            "is_energized": energized,
            "is_runi_staked": runi,
        },
    }


def test_worksite_capacity_is_fixed_five_without_powered_peers():
    target = _worksite(capacity=0)
    capacity_index = structure_capacity_index({"plots": [target]})

    assert capacity_index == ({}, {})
    assert potential_worker_capacity(target, capacity_index) == WORKSITE_WORKER_CAPACITY == 5


def test_worksite_peer_observations_do_not_decide_capacity():
    target = _worksite(capacity=0)
    contradictory_peer = _worksite(capacity=12, core=True)
    capacity_index = structure_capacity_index({"plots": [target, contradictory_peer]})

    assert capacity_index == ({}, {})
    assert potential_worker_capacity(target, capacity_index) == 5


@pytest.mark.parametrize("power_flag", ["is_power_core_staked", "is_energized"])
def test_live_full_power_source_gives_worksite_five_slots_without_peers(power_flag):
    target = _worksite(capacity=0)
    target["staking_facts"][power_flag] = True

    assert powered_worker_capacity(target, structure_capacity_index({"plots": [target]})) == 5
    assert _worker_slot_capacity(target, target["staking_facts"], ({}, {})) == 5


def test_uncleared_staged_worksite_and_staged_power_core_give_five_slots():
    uncleared = {
        "facts": {"worksite_type": ""},
        "worksite_facts": None,
        "staking_facts": {"max_workers_allowed": 0},
    }
    projected, target = _projected_worksite_row(uncleared, "grain")
    staged_flags = {**projected["staking_facts"], "is_power_core_staked": True}

    assert target == "grain"
    assert _worker_slot_capacity(projected, staged_flags, ({}, {})) == 5


def test_unpowered_worksite_has_zero_usable_slots():
    target = _worksite(capacity=0)

    assert potential_worker_capacity(target, ({}, {})) == 5
    assert _worker_slot_capacity(target, target["staking_facts"], ({}, {})) == 0


def test_runi_is_outside_five_regular_slots_but_alone_powers_only_four():
    target = _worksite(capacity=4, runi=True)

    assert potential_worker_capacity(target, ({}, {})) == 5
    assert powered_worker_capacity(target, ({}, {})) == 5
    assert powered_worker_slots(target, ({}, {})) == 4
    assert _worker_slot_capacity(target, target["staking_facts"], ({}, {})) == 4


def test_worker_slot_capacity_distinguishes_live_runi_from_staged_power_transitions():
    full_with_runi = _worksite(capacity=5, core=True, runi=True)
    after_core_removal = {**full_with_runi["staking_facts"], "is_power_core_staked": False}
    assert _worker_slot_capacity(full_with_runi, after_core_removal, ({}, {})) == 4

    energized_with_runi = _worksite(capacity=5, energized=True, runi=True)
    after_energized_removal = {**energized_with_runi["staking_facts"], "is_energized": False}
    assert _worker_slot_capacity(energized_with_runi, after_energized_removal, ({}, {})) == 4

    unpowered = _worksite(capacity=0)
    after_runi_stake = {**unpowered["staking_facts"], "is_runi_staked": True}
    assert _worker_slot_capacity(unpowered, after_runi_stake, ({}, {})) == 4

    runi_only = _worksite(capacity=4, runi=True)
    after_core_stake = {**runi_only["staking_facts"], "is_power_core_staked": True}
    assert _worker_slot_capacity(runi_only, after_core_stake, ({}, {})) == 5


@pytest.mark.parametrize("project_type", ["keep_operation", "castle_operation"])
def test_non_worksite_structure_still_inherits_observed_capacity_from_peer(project_type):
    evidence = {
        "facts": {"worksite_type": project_type.split("_", 1)[0].title()},
        "worksite_facts": {"project_type": project_type},
        "staking_facts": {"max_workers_allowed": 12, "is_power_core_staked": True},
    }
    target = {
        "facts": {"worksite_type": project_type.split("_", 1)[0].title()},
        "worksite_facts": None,
        "staking_facts": {"max_workers_allowed": 0},
    }
    report = {"plots": [evidence, target]}
    assert potential_worker_capacity(target, structure_capacity_index(report)) == 12


def test_invalid_or_extreme_capacity_evidence_fails_safe():
    target = {
        "facts": {"worksite_type": "Keep"},
        "worksite_facts": {"project_type": "future_structure"},
        "staking_facts": {"max_workers_allowed": 0},
    }
    for invalid in (True, False, "not-a-number", 5.5, float("inf"), 10_000_000, 10**1000):
        evidence = {
            "facts": {"worksite_type": "Keep"},
            "worksite_facts": {"project_type": "future_structure"},
            "staking_facts": {"max_workers_allowed": invalid, "is_power_core_staked": True},
        }
        assert potential_worker_capacity(target, structure_capacity_index({"plots": [evidence, target]})) == 0


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_detail_row(base_url: str, *, min_open_slots: int = 0) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("open_slots", 0) >= min_open_slots:
            deed_uid = plot["deed_uid"]
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot matched the requested open-slot minimum")


def test_unpowered_editor_projects_non_five_capacity_and_keeps_tiles_actionable(live_browser: tuple[Browser, str]) -> None:
    r"""Replaces the node-sliced `editorGroups()` capacity test (census row 20's
 first node test) with the real rendered editor for a plot that is fully
 unpowered (no Power Core, not Energized) but whose `capacity.
 potential_card_capacity` response is deliberately injected as 12: the editor
 must render 12 real, clickable (non-disabled) empty worker tiles despite having
 no power source, proving the generic non-worksite capacity contract and that the
 worker group stays `actionable: true` unconditionally.

 Named injection, run and watched red, then reverted and watched green: in
 `ceres/static/app.js`'s `editorGroups()`, the worker group's
 `actionable: true` literal was changed to
 `actionable: facts.is_power_core_staked === true || facts.is_energized === true`
 (ties tile actionability to power). Red: `AssertionError: assert not True ...
 is_disabled()` on the first empty tile -- it stayed a real `<button>` (the tag
 assertion passed) but rendered `disabled` because `group.actionable` was now
 false. Restoring the literal made this test pass again (whole-file run:
 6 passed)."""
    browser, base_url = live_browser
    row = _real_detail_row(base_url)
    deed_uid = row["deed_uid"]
    row["workers"] = []
    row["is_powered"] = False
    row["is_power_core_staked"] = False
    row["is_energized"] = False
    row["capacity"] = dict(row["capacity"], potential_card_capacity=12, runi_staked=False, open_slots=12, worker_count=0)

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    worker_group = page.locator(".slot-group[data-group-id='worker']")
    worker_group.wait_for(timeout=30_000)
    tiles = worker_group.locator(".slot-tile-cell .slot-tile")
    assert tiles.count() == 12
    for index in range(tiles.count()):
        tile = tiles.nth(index)
        assert tile.evaluate("el => el.tagName.toLowerCase()") == "button", "an unpowered plot's empty worker tile must stay a real, clickable button"
        assert not tile.is_disabled()
    page.close()


def test_extreme_projected_capacity_fails_safe_to_zero_slots(live_browser: tuple[Browser, str]) -> None:
    r"""Second half of census row 20's first node test: `editorGroups()`'s own
 integer/range guard on `capacity.potential_card_capacity` (independent of, and
 a second layer behind, the Python-side `potential_worker_capacity()` safety
 covered above) -- an absurd value must render ZERO worker tiles, not attempt
 to build an enormous grid.

 Named injection, run and watched red, then reverted and watched green: in
 `ceres/static/app.js`'s `editorGroups()`, the capacity guard
 `Number.isInteger(rawCapacity) && rawCapacity >= 0 && rawCapacity <= 50 ?
 rawCapacity : 0` had its `<= 50` upper bound removed (`Number.isInteger(rawCapacity)
 && rawCapacity >= 0 ? rawCapacity : 0`). Red: this test's page load hung/timed
 out building a 10,000,000-cell grid (`playwright._impl._errors.TimeoutError`
 waiting for `.slot-group[data-group-id='worker']`). Restoring the bound made
 this test pass again (whole-file run: 6 passed)."""
    browser, base_url = live_browser
    row = _real_detail_row(base_url)
    deed_uid = row["deed_uid"]
    row["workers"] = []
    row["capacity"] = dict(row["capacity"], potential_card_capacity=10_000_000, open_slots=0, worker_count=0)

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    worker_group = page.locator(".slot-group[data-group-id='worker']")
    worker_group.wait_for(timeout=30_000)
    tiles = worker_group.locator(".slot-tile-cell .slot-tile")
    assert tiles.count() == 0
    page.close()


def test_stage_reservation_reaches_every_picker_on_the_same_plot_and_releases_on_unstage(live_browser: tuple[Browser, str]) -> None:
    r"""Replaces the node-sliced `stagedIncomingAssetRefs()`/`candidateQuery()` test
 (census row 20's second node test). Real click path on a plot with 2 real open
 worker slots: staging a mocked candidate into slot 0 must make slot 1's picker
 request exclude that SAME asset_ref via `reserved_asset_ref` (so the same card
 cannot be picked into two slots at once); undoing the slot-0 stage must release
 the reservation, and slot 1's picker must no longer exclude it.

 Named injection, run and watched red, then reverted and watched green: in
 `ceres/static/app.js`, `stagedIncomingAssetRefs()`'s body was replaced with
 `return new Set();` (always reports nothing staged). Red:
 `AssertionError: assert None == ['reservation-test-card']` -- the second
 slot's request carried no `reserved_asset_ref` param at all. Restoring the
 function made this test pass again (whole-file run: 6 passed)."""
    browser, base_url = live_browser
    row = _real_detail_row(base_url, min_open_slots=2)
    deed_uid = row["deed_uid"]

    with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?group_id=worker&limit=5&offset=0&sort=construction_pp&bucket=all&preset=net_gain_default", timeout=10) as response:
        real_payload = json.loads(response.read().decode("utf-8"))
    candidate = copy.deepcopy(real_payload["candidates"][0])
    candidate["asset_ref"] = "reservation-test-card"
    candidate["uid"] = "reservation-test-card"

    candidate_requests: list[str] = []

    def fulfill_candidates(route) -> None:
        candidate_requests.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill_candidates)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    worker_group = page.locator(".slot-group[data-group-id='worker']")
    worker_group.wait_for(timeout=30_000)
    empty_tiles = worker_group.locator(".slot-tile-cell .slot-tile.is-empty")
    assert empty_tiles.count() >= 2, "need 2 real open worker slots on this plot to prove cross-slot reservation"

    empty_tiles.nth(0).click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)
    page.locator(".candidate-row").first.click()
    page.wait_for_function("document.querySelectorAll('.slot-group[data-group-id=\\'worker\\'] .slot-tile.is-staged').length > 0", timeout=30_000)

    candidate_requests.clear()
    empty_tiles = worker_group.locator(".slot-tile-cell .slot-tile.is-empty")
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        empty_tiles.first.click()
    page.wait_for_timeout(300)
    assert candidate_requests, "opening the second empty slot's picker made no candidates request"
    reserved_params = urllib.parse.parse_qs(urllib.parse.urlparse(candidate_requests[-1]).query)
    assert reserved_params.get("reserved_asset_ref") == ["reservation-test-card"], reserved_params
    page.keyboard.press("Escape")

    staged_tile = worker_group.locator(".slot-tile.is-staged").first
    staged_tile.hover()
    staged_tile.locator(".slot-remove").click(force=True)
    page.wait_for_function("document.querySelectorAll('.slot-group[data-group-id=\\'worker\\'] .slot-tile.is-staged').length === 0", timeout=30_000)

    candidate_requests.clear()
    empty_tiles = worker_group.locator(".slot-tile-cell .slot-tile.is-empty")
    with page.expect_request(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*"):
        empty_tiles.first.click()
    page.wait_for_timeout(300)
    assert candidate_requests
    released_params = urllib.parse.parse_qs(urllib.parse.urlparse(candidate_requests[-1]).query)
    assert "reservation-test-card" not in released_params.get("reserved_asset_ref", [])
    page.close()


def test_worker_power_warning_computation_is_dead_code_not_rendered_anywhere():
    """Deleted, per dispatcher ruling (Phase 1 queue F2, §15
 pattern): a test asserting the shape of dead code guards nothing. Dead
 symbol: `powerWarning`, computed at `ceres/static/app.js:4656-4657` inside
 `renderSlotEditor()` (`const powerWarning = hasPowerConcern &&
 !reviewedPowerNotice ? renderNotices(...) : '';`) and READ NOWHERE ELSE in
 the file -- grep confirms exactly one other occurrence of the identifier
 outside its own declaration line, a comment at `app.js:4480` distinguishing
 it from an unrelated notice. No template literal in `renderSlotEditor()`'s
 return path references it; the `workers_staged_without_power` advisory it
 would have produced never reaches the DOM.

 An earlier queue found this (see this file's module-header comment above)
 and left the source-text assertions in place, STOPPED, pending an maintainer ruling
 on whether to wire the advisory up or delete the computation. That ruling
 has now been made by the dispatcher: delete.

 This test's fifth assertion -- `"id: 'worker', label: 'Workers', capacity,
 actionable: true" in source` -- named LIVE code (`editorGroups()`,
 `app.js:3526`), but its observable behaviour (the worker group renders as
 actionable regardless of power state) is already covered as rendered DOM,
 not source text, by this file's own
 `test_unpowered_editor_projects_non_five_capacity_and_keeps_tiles_actionable`
 above (12 real, non-disabled `<button>` tiles on a fully unpowered plot).
 Not duplicated here.

 Manifest note: `powerWarning` (app.js:4656) and the `hasPowerConcern`/
 `stagedPowered` locals it depends on move nowhere on their own merit in the
 app.js split -- they are read by nothing and should be marked for removal,
 not relocation, unless the maintainer instead chooses to wire
 `workers_staged_without_power` into `renderSlotEditor()`'s return path
 (unchanged from the earlier queue's finding)."""
