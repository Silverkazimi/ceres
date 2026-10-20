""" -- verifies the sole-power-source hazard actually fires for a Runi removal
through the real click-handler path, not just through op_preview.py's build_preview().

This hazard math (`ceres.op_preview.sole_power_source_removed`) was written and unit-
tested while Runi staging was still refused (`STK-LND-RUNI` was in `UNVERIFIED_TYPES`),
so the CLIENT-SIDE gate that opens the confirmation modal before a single-tile "remove"
click reaches the server had never executed in production. That gate is
`isSoleSource(row, group.id)` in ceres/static/app.js, called from
`handleSlotEditorClick()` right before `stagePricedRemovals()`. Converted (census row 21)
to a real-browser test: a real plot's remove button is clicked, and the REAL hazard
overlay (`#slot-hazard-overlay`, `ensureHazardOverlay()`/`openHazard()`) is asserted
visible or absent, proving the modal opens for a Runi exactly as it already does for a
Power Core, and does NOT open when another source (energized, or the other of the two)
still covers the plot.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres, wait_ready


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_row_with_runi(base_url: str) -> dict:
    """A REAL `GET /api/plots/{deed}` row for a plot with a real Runi staked
    (`workers` slot 0) -- used as a template; only the power-source flags and the
    Runi's presence are overridden per scenario."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("runi_staked"):
            deed_uid = plot["deed_uid"]
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot has a real Runi staked to build a fixture from")


REMOVAL_IMPACT_RESPONSE_TEMPLATE = {
    "before_powered": True, "after_powered": True, "removed": [],
    "plot_residual_delta": 0.0, "plot_ability_delta": 0.0, "harvest_pp_delta": 0.0,
    "own_construction_pp_delta": {"status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None},
    "own_base_pp_delta": {"status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None},
    "notices": [],
}


def _drive_remove_click(page, base_url: str, row: dict, group_id: str, *, expect_hazard: bool) -> None:
    deed_uid = row["deed_uid"]
    removed_asset_ref = next(w["asset_ref"] for w in row["workers"] if (group_id == "runi") == (w["slot"] == 0))
    removal_response = dict(REMOVAL_IMPACT_RESPONSE_TEMPLATE, deed_uid=deed_uid, removed=[
        {"asset_ref": removed_asset_ref, "name": "removed", "own_harvest_pp": 0.0, "own_construction_pp": 0.0, "own_base_pp": 0.0},
    ])
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/removal-impact*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(removal_response)),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    tile = page.locator(f".slot-power .slot-tile[data-group-id='{group_id}']").first
    tile.wait_for(timeout=30_000)
    page.locator(".slot-power").first.hover()
    tile.hover()
    wait_ready(page)
    settle(page)
    tile.locator(".slot-remove").click(force=True, timeout=10_000)
    overlay = page.locator("#slot-hazard-overlay")
    if expect_hazard:
        overlay.wait_for(timeout=30_000, state="visible")
        assert "sole power source" in overlay.inner_text().lower()
    else:
        settle(page)
        assert overlay.count() == 0 or not overlay.is_visible(), "hazard modal opened even though another source covers the plot"


def test_runi_removal_opens_hazard_modal_when_it_is_the_sole_power_source(live_browser: tuple[Browser, str]) -> None:
    r"""Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `isSoleSource()`, the `runi` branch
    `return hasRuni && !hasEnergized && !hasCore;` was changed to `return false;`.
    Red: `playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 30000ms
    exceeded ... waiting for locator("#slot-hazard-overlay") to be visible` -- the
    click fell through to the direct `stagePricedRemovals()` path instead.
    Restoring the line made this test pass again (whole-file run: 4 passed)."""
    browser, base_url = live_browser
    row = _real_row_with_runi(base_url)
    row["is_power_core_staked"] = False
    row["is_energized"] = False
    row["capacity"] = dict(row["capacity"], runi_staked=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _drive_remove_click(page, base_url, row, "runi", expect_hazard=True)
    page.close()


def test_power_core_removal_opens_hazard_modal_when_it_is_the_sole_power_source(live_browser: tuple[Browser, str]) -> None:
    r"""Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `isSoleSource()`, the `power_core` branch
    `return hasCore && !hasEnergized && !hasRuni;` was changed to `return false;`.
    Red: `playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 30000ms
    exceeded ... waiting for locator("#slot-hazard-overlay") to be visible`.
    Restoring the line made this test pass again (whole-file run: 4 passed)."""
    browser, base_url = live_browser
    row = _real_row_with_runi(base_url)
    row["workers"] = [w for w in row["workers"] if w["slot"] != 0]
    row["is_power_core_staked"] = True
    row["is_energized"] = False
    row["capacity"] = dict(row["capacity"], runi_staked=False)
    row["bonus"] = dict(row.get("bonus") or {}, power_core={"asset_ref": "core-1", "uid": "core-1", "name": "Power Core"})
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _drive_remove_click(page, base_url, row, "power_core", expect_hazard=True)
    page.close()


def test_runi_removal_does_not_open_hazard_modal_when_energized_covers_the_plot(live_browser: tuple[Browser, str]) -> None:
    r"""Named injection, shared with the sibling test below, run and watched red,
    then reverted and watched green: `isSoleSource()`'s `runi` branch was changed
    to `return hasRuni;` (drops the `!hasEnergized && !hasCore` exclusions, so ANY
    Runi is reported "sole"). Red: `AssertionError: hazard modal opened even
    though another source covers the plot` -- `assert (1 == 0 or not True)`.
    Restoring the line made this test pass again (whole-file run: 4 passed)."""
    browser, base_url = live_browser
    row = _real_row_with_runi(base_url)
    row["is_power_core_staked"] = False
    row["is_energized"] = True
    row["capacity"] = dict(row["capacity"], runi_staked=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _drive_remove_click(page, base_url, row, "runi", expect_hazard=False)
    page.close()


def test_runi_removal_does_not_open_hazard_modal_when_a_power_core_covers_the_plot(live_browser: tuple[Browser, str]) -> None:
    r"""Named injection: same mutation as the sibling test above (`isSoleSource()`'s
    `runi` branch changed to `return hasRuni;`), run and watched red (identical
    failure: hazard modal opened despite the Power Core covering the plot), then
    reverted and watched green (whole-file run: 4 passed)."""
    browser, base_url = live_browser
    row = _real_row_with_runi(base_url)
    row["is_power_core_staked"] = True
    row["is_energized"] = False
    row["capacity"] = dict(row["capacity"], runi_staked=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _drive_remove_click(page, base_url, row, "runi", expect_hazard=False)
    page.close()
