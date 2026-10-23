"""Rendered DOM checks for the worksite modal and header tooltip rewrite."""

from __future__ import annotations

import copy
import json
import re

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres


VIEWPORT = {"width": 1920, "height": 1080}


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _tile(target: str, current: bool = False) -> dict:
    rate = {"value": 1.25, "unit": "per hour", "projected_harvest_pp": 1250}
    if target == "sps":
        rate.update({"kind": "pool_share", "value": None})
    tile = {
        "target": target,
        "worksite_name": target.title(),
        "is_current": current,
        "needs_inputs": ["grain"],
        "rate": rate,
    }
    if target == "wood":
        tile["dstb_notice"] = {
            "code": "worksite_swap_bonus_gained",
            "context": {"status": "occupied", "worksite": "Logging Camp"},
        }
    return tile


def _apply_choose_state(row: dict) -> dict:
    result = copy.deepcopy(row)
    result["terrain"] = "Plains"
    result["assets_loaded"] = True
    result.setdefault("capacity", {})
    result["capacity"].update({"max_workers_allowed": 5, "worker_count": 1, "power_core_staked": True})
    result["workers"] = result.get("workers") or []
    result["swap_targets"] = [_tile("grain", True), _tile("sps"), _tile("wood")]
    result["swap_eligibility"] = {"eligible": True}
    result["worksite_completion"] = None
    return result


def _page(browser: Browser, base_url: str) -> Page:
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=30_000,
    )
    return page


def _open_modal(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    selected: dict[str, str] = {}

    def board_handler(route) -> None:
        response = route.fetch()
        payload = response.json()
        index = next(i for i, plot in enumerate(payload["plots"]) if plot.get("deed_uid"))
        row = _apply_choose_state(payload["plots"][index])
        selected["uid"] = row["deed_uid"]
        payload["plots"][index] = row
        route.fulfill(response=response, body=json.dumps(payload))

    def plot_handler(route) -> None:
        uid = selected.get("uid")
        path = route.request.url.split("?", 1)[0]
        if uid and (path.endswith(f"/api/plots/{uid}") or path.endswith(f"/api/plots/{uid}/expand")):
            response = route.fetch()
            route.fulfill(response=response, body=json.dumps(_apply_choose_state(response.json())))
            return
        route.continue_()

    page.route("**/api/board*", board_handler)
    page.route("**/api/plots/**", plot_handler)
    page.goto(base_url)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=30_000,
    )
    row = page.locator(f'tr.plot-row[data-deed-uid="{selected["uid"]}"]')
    row.click()
    expanded = page.locator(f'tr.expand-row[data-deed-uid="{selected["uid"]}"] .slot-editor')
    expanded.wait_for(timeout=30_000)
    page.locator(f'tr.expand-row[data-deed-uid="{selected["uid"]}"] button.worksite-change-btn').click()
    page.locator("#worksite-modal-overlay").wait_for(state="visible", timeout=10_000)
    return page


def test_header_controls_use_rendered_tooltip_attributes(live_browser) -> None:
    """Mutation: restore a native title or omit a body/ARIA value; the rendered selector assertion names that control.

    The header no longer has "Reload plots" (removed; test_r111_property_guards.py keeps
    `#reload-btn` out) or the Combines filter. Its tooltip-bearing controls with a body are
    the player switch and Harvest, which must keep the same rendered contract."""
    page = _page(*live_browser)
    expected = {
        ".player-switch": (
            "Switch player",
            "Type a player's name and press Enter to load their plots instead.",
        ),
        "#harvest-btn": (
            "Harvest",
            "Review and harvest your regions.",
        ),
    }
    try:
        for selector, (title, body) in expected.items():
            element = page.locator(selector)
            assert element.get_attribute("data-tip") == title, selector
            assert element.get_attribute("data-tip-body") == body, selector
            aria_label = element.get_attribute("aria-label") or ""
            assert title in aria_label and body in aria_label, selector
            assert element.get_attribute("title") is None, selector
    finally:
        page.close()


def test_disabled_worksite_confirm_has_one_shared_rendered_name(live_browser) -> None:
    """Mutation: restore either old Confirm string or omit the shared call-path value; the disabled rendered button assertion fails."""
    page = _open_modal(live_browser)
    try:
        confirm = page.locator(".worksite-modal-confirm")
        assert confirm.is_disabled()
        assert confirm.get_attribute("data-tip") == "Choose a worksite first"
        assert confirm.get_attribute("aria-label") == "Choose a worksite first"
        assert page.locator('[data-tip="Choose a worksite first"]').count() == 1
        assert page.locator('[aria-label="Choose a worksite first"]').count() == 1
    finally:
        page.close()


def test_worksite_tile_key_is_only_present_for_a_solo_base_rate(live_browser) -> None:
    """Mutation: assign a key to every tile or keep it after an extra line; the rendered key assertions fail for SPS or Wood."""
    page = _open_modal(live_browser)
    try:
        grain = page.locator('.worksite-tile[data-worksite-target="grain"]')
        sps = page.locator('.worksite-tile[data-worksite-target="sps"]')
        wood = page.locator('.worksite-tile[data-worksite-target="wood"]')
        assert grain.get_attribute("data-tip-key") == "50 PP per Grain/hour"
        assert sps.get_attribute("data-tip-key") is None
        assert wood.get_attribute("data-tip-key") is None
    finally:
        page.close()


def test_modal_and_header_tooltip_attributes_forbid_served_and_raw(live_browser) -> None:
    """Mutation: inject either forbidden word into a tooltip attribute; the rendered page-wide attribute sweep reports its maintainer."""
    page = _page(*live_browser)
    try:
        values = page.locator("header [data-tip], header [data-tip-body], header [data-tip-foot], header [aria-label], header [title]").evaluate_all(
            "elements => elements.flatMap(element => ['data-tip', 'data-tip-body', 'data-tip-foot', 'aria-label', 'title'].map(name => element.getAttribute(name)).filter(Boolean))"
        )
        page.close()
        page = _open_modal(live_browser)
        values.extend(page.locator("#worksite-modal-overlay [data-tip], #worksite-modal-overlay [data-tip-body], #worksite-modal-overlay [data-tip-foot], #worksite-modal-overlay [aria-label], #worksite-modal-overlay [title]").evaluate_all(
            "elements => elements.flatMap(element => ['data-tip', 'data-tip-body', 'data-tip-foot', 'aria-label', 'title'].map(name => element.getAttribute(name)).filter(Boolean))"
        ))
        assert not any(re.search(r"\b(served|raw)\b", value, re.IGNORECASE) for value in values), values
    finally:
        page.close()
