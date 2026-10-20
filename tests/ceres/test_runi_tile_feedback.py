"""Coverage for the Runi tile-feedback bug (reported live 2026-08-26): clicking the `x`
on a staked Runi staged the removal correctly (checkout updated, priced) but the tile
itself showed no visible change, unlike every other asset class.

Root cause: `editorGroups()`'s Runi slot fell back to `row.bonus.runi === true` --
the server's ORIGINAL, never-mutated snapshot -- whenever `row.workers` had no slot-0
entry. `mutateEditorSlot()`'s Runi branch () deliberately never touches
`row.bonus.runi` (it flips `row.capacity.runi_staked` instead, to avoid breaking the
boolean power-source checks elsewhere), so after a staged removal `row.bonus.runi` was
still `true` and the fallback kept resolving a bare stub occupant
(`{name: 'Runi', card_name: 'Runi', ...}`) instead of `null` -- so the tile never
reached the `!occupant` branch that renders `.is-pending-removal`, and instead kept
rendering as an ordinary (degraded) occupied tile.

A second, related bug in `restoreStagedMove()` (the "click to undo" path) is covered
here too: it routed Runi through the generic `row.bonus[group.id] = previous` branch
meant for Totem/Title/Power Core, which left `row.workers`/`row.capacity.runi_staked`
untouched -- so undoing a staged Runi removal rendered an empty "Add Runi" tile instead
of bringing the card back.

Both are sliced straight out of app.js and exercised in node (mirrors
tests/ceres/test_job2_staged_undo.py and test_restore_removed_tile.py), not
reimplemented. Live-browser verification against the real board lives in the
session's report, per this project's rule that a passing node-level test is not
acceptance on its own for a visual bug.
"""

from __future__ import annotations

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
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("runi_staked"):
            deed_uid = plot["deed_uid"]
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot has a real Runi staked to build a fixture from")


REMOVAL_IMPACT_TEMPLATE = {
    "before_powered": True, "after_powered": True, "removed": [],
    "plot_residual_delta": 0.0, "plot_ability_delta": 0.0, "harvest_pp_delta": 0.0,
    "own_construction_pp_delta": {"status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None},
    "own_base_pp_delta": {"status": "point", "value": 0.0, "low": 0.0, "high": 0.0, "reason": None},
    "notices": [],
}


def test_runi_removal_marks_pending_removal_and_restore_repopulates_the_tile(live_browser: tuple[Browser, str]) -> None:
    r"""Replaces both node-sliced `editorGroups()`/`mutateEditorSlot()`/
    `restoreStagedMove()` tests (census row 32) with the real click round-trip on a
    real Runi-staked plot: click the Runi tile's remove button (with another power
    source present so this doesn't route through the hazard modal -- that's
    test_runi_hazard.py's own subject) and read the REAL rendered tile,
    then click it again to restore.

    Named injection #1, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `editorGroups()`, the Runi slot's fallback condition
    `facts.is_runi_staked === true || row.capacity?.runi_staked === true` (the
    live, staging-aware check) was changed to `bonus.runi === true` -- the exact
    never-cleared stale stub this file exists to guard against. Red:
    `playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 30000ms
    exceeded ... waiting for locator(".slot-power .slot-tile[data-group-id='runi']
    [data-slot-action='restore']") to be visible` -- the stub occupant resolved
    again instead of null, so the tile never reached the pending-removal render
    branch. Restoring the line made this test pass again.

    Named injection #2, run and watched red, then reverted and watched green: in
    `restoreStagedMove()`, the Runi-specific branch (writing `row.workers`/
    `row.capacity.runi_staked`) was removed so Runi fell through to the generic
    `row.bonus[group.id] = previous` branch. Red: after clicking restore, the tile
    stayed on the empty "Add Runi" button (`AssertionError:
    Locator.wait_for: Timeout ... waiting for locator(".slot-power .slot-tile[data-
    group-id='runi']:not(.is-empty)")`) instead of showing Runi again. Restoring
    the branch made this test pass again (whole-file run: 2 passed)."""
    browser, base_url = live_browser
    row = _real_row_with_runi(base_url)
    deed_uid = row["deed_uid"]
    row["is_power_core_staked"] = True  # not the sole source -- no hazard modal
    row["is_energized"] = False
    runi_asset_ref = next(w["asset_ref"] for w in row["workers"] if w["slot"] == 0)

    removal_response = dict(REMOVAL_IMPACT_TEMPLATE, deed_uid=deed_uid, removed=[
        {"asset_ref": runi_asset_ref, "name": "Runi", "own_harvest_pp": 0.0, "own_construction_pp": 0.0, "own_base_pp": 0.0},
    ])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
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
    runi_tile = page.locator(".slot-power .slot-tile[data-group-id='runi']").first
    runi_tile.wait_for(timeout=30_000)
    page.locator(".slot-power").first.hover()
    runi_tile.hover()
    wait_ready(page)
    settle(page)
    runi_tile.locator(".slot-remove").click(force=True, timeout=10_000)

    pending_tile = page.locator(".slot-power .slot-tile[data-group-id='runi'][data-slot-action='restore']")
    pending_tile.wait_for(timeout=30_000)
    assert "is-pending-removal" in (pending_tile.get_attribute("class") or "")

    pending_tile.click()
    restored_tile = page.locator(".slot-power .slot-tile[data-group-id='runi']:not(.is-empty)").first
    restored_tile.wait_for(timeout=30_000)
    assert "is-pending-removal" not in (restored_tile.get_attribute("class") or "")
    assert "runi" in restored_tile.inner_text().lower()
    page.close()


def test_runi_tooltip_leads_with_boost_and_omits_placeholders(live_browser: tuple[Browser, str]) -> None:
    r"""Second, related defect reported the same day (census row 32): the Runi
    tile's tooltip used to fall through the generic monster-card template, which
    for a Runi renders misleading boilerplate and omits everything that matters.
    Drives the real rendered tooltip (`aria-label`) for a real Runi tile in two
    plot states: covered by another power source (no "sole" line) and sole-power
    (the "sole" line appears).

    Named injection, run and watched red, then reverted and watched green: in
    `ceres/static/app.js`'s `editorRuniTip()`, the line
    `if (isSoleSource(row, 'runi')) lines.push(...'sole proven power source');`
    was changed to `if (true) lines.push(...)`. Red: the covered-plot assertion
    (`assert "sole proven power source" not in aria`) failed. Restoring the
    condition made this test pass again (whole-file run: 2 passed)."""
    browser, base_url = live_browser
    row = _real_row_with_runi(base_url)
    deed_uid = row["deed_uid"]

    row["is_power_core_staked"] = True
    row["is_energized"] = False
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    runi_tile = page.locator(".slot-power .slot-tile[data-group-id='runi']").first
    runi_tile.wait_for(timeout=30_000)
    covered_title = (runi_tile.get_attribute("data-tip") or "").lower()
    covered_key = runi_tile.get_attribute("data-tip-key") or ""
    covered_body = (runi_tile.get_attribute("data-tip-body") or "").lower()
    assert "runi" in covered_title
    assert covered_key == "100%"
    assert "production boost" not in covered_body
    assert "sole proven power source" not in covered_body
    page.close()

    row["is_power_core_staked"] = False
    page2 = browser.new_page(viewport={"width": 1400, "height": 900})
    page2.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page2.goto(base_url)
    page2.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page2.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    runi_tile2 = page2.locator(".slot-power .slot-tile[data-group-id='runi']").first
    runi_tile2.wait_for(timeout=30_000)
    sole_aria = (runi_tile2.get_attribute("aria-label") or "").lower()
    assert "sole proven power source" in sole_aria
    page2.close()
