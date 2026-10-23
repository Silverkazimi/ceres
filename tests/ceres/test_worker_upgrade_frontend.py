from __future__ import annotations

import copy
import json
import re
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres

# (): this file used to load `app.js` as text and
# slice individual functions out of it by string position -- forbidden by the
# campaign ruling (assert on rendered DOM, not on source spelling). No test
# in this file reads its contents anymore; the dead-code finding below cites
# the running source by line number instead of keeping an unread path
# constant here.


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


def test_upgrade_chip_renders_only_for_the_eligible_worker_not_the_maxed_one(
    live_browser: tuple[Browser, str],
) -> None:
    r"""Replaces the two node-sliced `workerUpgradeIsAvailable()` tests (census row 42's
    node tests: `test_maxed_worker_has_no_upgrade_affordance` and
    `test_maxed_worker_affordance_guard_mutation_goes_red`) with the real rendered
    affordance. `workerUpgradeIsAvailable` is live -- called from
    `workerUpgradeCopyCount()` (`ceres/static/app.js:417-433`), which the real
    candidate-row renderer calls at `app.js:4324` to produce the `.candidate-upgrade`
    "N combinable copies owned" chip next to a picker candidate that duplicates one of
    the plot's own staked workers.

    Setup: a plot with two staked workers occupying slots 1-2 of a 3-capacity worker
    group (one open slot) -- `maxed-worker` (`bcx: 10, bcx_max: 10`, at its known
    ceiling) and `eligible-worker` (`bcx: 3, bcx_max: None`, headroom). Each worker's
    mocked `/workers/<asset_ref>/upgrade-options` response reports `eligible: true`
    with one combinable other copy matching one of two mocked picker candidates by
    identity (`card_detail_id`/`edition`/foil). Opening the empty slot's picker must
    show the chip on the candidate matching the eligible worker and NOT on the
    candidate matching the maxed one -- proving `workerUpgradeIsAvailable`'s own
    `bcx >= bcx_max` ceiling gates the chip even though the maxed worker's own
    upgrade-options response claims `eligible: true`.

    Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `workerUpgradeIsAvailable()`, `return !(Number.isFinite(bcx)
    && Number.isFinite(bcxMax) && bcx >= bcxMax);` was replaced with `return true;`.
    Red: the maxed candidate's row rendered a `.candidate-upgrade` chip it must not
    have -- `assert maxed_chip.count() == 0` failed (count was 1). Restoring the
    ceiling check made this test pass again (whole-file run: 2 passed)."""
    browser, base_url = live_browser
    row = _real_detail_row(base_url)
    deed_uid = row["deed_uid"]

    with urllib.request.urlopen(
        f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates"
        "?group_id=worker&limit=5&offset=0&sort=construction_pp&bucket=all&preset=net_gain_default",
        timeout=10,
    ) as response:
        real_payload = json.loads(response.read().decode("utf-8"))
    template = real_payload["candidates"][0]

    maxed_candidate = copy.deepcopy(template)
    maxed_candidate.update(asset_ref="maxed-match-card", uid="maxed-match-card", card_detail_id=101, edition=1, foil=False, gold=False)
    eligible_candidate = copy.deepcopy(template)
    eligible_candidate.update(asset_ref="eligible-match-card", uid="eligible-match-card", card_detail_id=202, edition=2, foil=False, gold=False)

    maxed_worker = {
        "asset_ref": "maxed-worker", "slot": 1, "name": "Maxed Worker",
        "bcx": 10, "bcx_max": 10, "card_detail_id": 501, "edition": 1, "foil": False,
    }
    eligible_worker = {
        "asset_ref": "eligible-worker", "slot": 2, "name": "Eligible Worker",
        "bcx": 3, "bcx_max": None, "card_detail_id": 502, "edition": 1, "foil": False,
    }
    row = dict(row)
    row["workers"] = [maxed_worker, eligible_worker]
    row["capacity"] = dict(row["capacity"], potential_card_capacity=3, open_slots=1, worker_count=2)

    def fulfill_options(route, *, worker: dict, options: dict) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(options))

    def fulfill_candidates(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [maxed_candidate, eligible_candidate], "total": 2,
            "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        re.compile(rf"/api/plots/{re.escape(urllib.parse.quote(deed_uid, safe=''))}(/expand)?(\?|$)"),  # GET detail or POST expand
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/workers/maxed-worker/upgrade-options*",
        lambda route: fulfill_options(route, worker=maxed_worker, options={
            "eligible": True, "combinable_count": 1,
            "other_copies": [{"card_detail_id": 101, "edition": 1, "foil": False, "eligible": True}],
        }),
    )
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/workers/eligible-worker/upgrade-options*",
        lambda route: fulfill_options(route, worker=eligible_worker, options={
            "eligible": True, "combinable_count": 1,
            "other_copies": [{"card_detail_id": 202, "edition": 2, "foil": False, "eligible": True}],
        }),
    )
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill_candidates)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    worker_group = page.locator(".slot-group[data-group-id='worker']")
    worker_group.wait_for(timeout=30_000)
    worker_group.locator(".slot-tile-cell .slot-tile.is-empty").first.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)

    maxed_row = page.locator('.candidate-row[data-candidate-index="0"]')
    eligible_row = page.locator('.candidate-row[data-candidate-index="1"]')
    # Upgrade options are fetched after the list is drawn (deferred, R-138); read the chips
    # once those fetches are done, not on the first paint.
    settle(page)
    assert maxed_row.locator(".candidate-upgrade").count() == 0, "maxed worker's matching candidate must not show the upgrade chip"
    eligible_chip = eligible_row.locator(".candidate-upgrade")
    assert eligible_chip.count() == 1, "eligible worker's matching candidate must show exactly one upgrade chip"
    # chip wording is "<n> combinable copy/copies" (candidates.js renderCandidateRow; "owned" was dropped)
    assert eligible_chip.inner_text().strip() == "1 combinable copy"
    page.close()
