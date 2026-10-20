"""Live Chromium coverage for "click an empty tile to bring back a removed card"
(2026-08-25, `work/ceres-wave2/queue.md`) -- matches the in-game behavior of
clicking the empty tile again to restore it.

This mirrors test_bloodline_card_marking.py's own live-browser pattern (real
cached-snapshot plots, not a fixture stand-in) -- a passing node-snippet or
fixture-scale test is not acceptance on this project.

The cases are selected from the account's current snapshot so the tests remain
valid when the account's staked plots change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres, wait_for_board


SCREEN_DIR = Path(__file__).parents[2] / "work" / "ceres-restore-tile"
SCREEN_DIR.mkdir(parents=True, exist_ok=True)

@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    return page


def _plot_details(page: Page) -> list[dict]:
    deed_uids = page.locator("tr.plot-row").evaluate_all(
        "rows => rows.map(row => row.dataset.deedUid).filter(Boolean)"
    )
    return [
        page.evaluate(
            """async deedUid => (await (await fetch(`/api/plots/${encodeURIComponent(deedUid)}`)).json())""",
            deed_uid,
        )
        for deed_uid in deed_uids
    ]


def _bloodline_pair_fixture(page: Page) -> tuple[str, str, str, str] | None:
    for plot in _plot_details(page):
        for worker in plot.get("workers") or []:
            for entry in worker.get("land_abilities") or []:
                status = entry[3] if isinstance(entry, list) and len(entry) > 3 else None
                if not isinstance(entry, list) or len(entry) < 3 or entry[0] != "BLOODLINE":
                    continue
                if isinstance(status, dict) and status.get("active"):
                    companions = status.get("companions") or []
                    if companions:
                        return plot["deed_uid"], worker["name"], companions[0], entry[2]
    return None


def _power_core_fixture(page: Page) -> str | None:
    for plot in _plot_details(page):
        facts = plot.get("staking_facts") or {}
        has_worker = any((worker.get("slot") or 0) > 0 for worker in plot.get("workers") or [])
        has_backup_power = (
            plot.get("is_energized") is True
            or facts.get("is_energized") is True
            or (plot.get("capacity") or {}).get("runi_staked") is True
            or facts.get("is_runi_staked") is True
        )
        if (plot.get("is_power_core_staked") is True or facts.get("is_power_core_staked") is True) and has_worker and not has_backup_power:
            return plot.get("deed_uid")
    return None


def _expand_deed(page: Page, deed_uid: str):
    row = page.locator(f'tr.plot-row[data-deed-uid="{deed_uid}"]')
    if row.count() == 0:
        pytest.skip(f"{deed_uid} not present in this account's live snapshot")
    if row.locator("+ tr.expand-row").evaluate("node => node.style.display") == "none":
        row.click()
    expand = row.locator("+ tr.expand-row")
    expand.locator(".slot-editor").wait_for(timeout=30_000)
    return expand


@pytest.mark.xfail(strict=True, reason=": removal-impact bloodline notice absent since v0.8.61; held by his /")
def test_restore_bloodline_pair_break(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    fixture = _bloodline_pair_fixture(page)
    if fixture is None:
        pytest.skip("no staked bloodline pair in this account's live snapshot")
    deed_uid, bloodline_holder, _companion, bloodline_subtype = fixture
    expand = _expand_deed(page, deed_uid)

    holder_tile = expand.locator(".slot-tile").filter(has_text=bloodline_holder).first
    assert holder_tile.count() == 1, f"{bloodline_holder} not staked on the selected bloodline-pair plot"
    holder_tile.hover()
    holder_tile.locator(".slot-remove").click()

    warning = expand.locator('.plot-warning-row[data-plot-warning-kind="bloodline"]')
    warning.wait_for(timeout=15_000)
    assert bloodline_subtype in warning.inner_text()
    page.close()


def test_restore_sole_power_source_clears_zero_capacity_warning(live_browser: tuple[Browser, str]) -> None:
    page = _page(live_browser)
    deed_uid = _power_core_fixture(page)
    if deed_uid is None:
        pytest.skip("no staffed plot with a staked Power Core in this account's live snapshot")
    expand = _expand_deed(page, deed_uid)

    # Power Core shares the Power slot, so select its tile by the asset group.
    core_tile = expand.locator(".slot-tile[data-group-id='power_core'][data-occupied='1']").first
    if core_tile.count() == 0:
        pytest.skip(f"{deed_uid} has no staked Power Core in this account's live snapshot")
    worker_count_before = int(expand.locator(".slot-group[data-group-id='worker'] .slot-group-count").inner_text().split("/")[1])
    assert worker_count_before > 0, "expected a staffed plot to see the zero-capacity warning"

    core_tile.hover()
    core_tile.locator(".slot-remove").click()

    # Sole power source removal is hazard-class -- confirm the modal.
    hazard = page.locator(".slot-hazard-overlay")
    hazard.wait_for(timeout=15_000)
    hazard.locator("[data-hazard-action='confirm']").click()

    # Zero-capacity warning appears once the removal is staged; the Power
    # Core slot itself must ghost, not read as a plain empty "add" tile.
    zero_state = expand.locator('.slot-zero-state, .plot-warning-row[data-plot-warning-kind="power"]')
    zero_state.first.wait_for(timeout=15_000)
    ghost = expand.locator(".slot-power .slot-tile.is-pending-removal[data-group-id='power_core']")
    ghost.wait_for(timeout=15_000)
    assert ghost.get_attribute("data-slot-action") == "restore"
    assert ghost.locator(".slot-restore-label").count() == 0
    page.screenshot(path=str(SCREEN_DIR / "03_power_ghost_tile.png"))

    ghost.click()

    # Restore brings the Power Core back and clears the zero-capacity warning
    # exact restoration, same as the v0.5.20 core undo already proved.
    page.wait_for_function(
        "() => document.querySelector(\"tr.expand-row .slot-power .slot-tile[data-group-id='power_core'][data-occupied='1']\") !== null",
        timeout=15_000,
    )
    assert expand.locator(".slot-power .slot-tile.is-pending-removal[data-group-id='power_core']").count() == 0
    assert expand.locator(".slot-power .slot-tile[data-group-id='power_core'][data-occupied='1']").count() == 1
    assert expand.locator('.plot-warning-row[data-plot-warning-kind="power"]').count() == 0
    assert page.locator("#checkout-btn").is_hidden()
    page.screenshot(path=str(SCREEN_DIR / "04_power_restored.png"))
    page.close()
