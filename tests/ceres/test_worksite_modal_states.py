"""Route-mocked browser guards for the worksite modal body states."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres
from .vm_context import read_entry_source

ROOT = Path(__file__).parents[2]
APP = ROOT / "ceres" / "static" / "app.js"
ASSETS = ROOT / "ceres" / "static" / "assets"
TERRAINS = [
    "badlands", "bog", "caldera", "canyon", "desert", "forest", "hills",
    "jungle", "lake", "mountain", "plains", "river", "swamp", "tundra",
]
LEGAL_TARGETS = {
    "grain": {"bog", "lake", "plains", "river"},
    "wood": {"forest", "jungle", "swamp", "tundra"},
    "stone": {"canyon", "desert", "hills"},
    "iron": {"badlands", "caldera", "mountain"},
    "research": set(TERRAINS),
    "sps": set(TERRAINS),
    "aura": set(TERRAINS),
}


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _tile(target: str, current: bool = False) -> dict:
    return {
        "target": target,
        "worksite_name": target.title(),
        "is_current": current,
        "needs_inputs": ["grain"],
        "rate": {"value": 1.25, "unit": "per hour", "projected_harvest_pp": 1250},
    }


def _apply_state(row: dict, state: str) -> dict:
    result = copy.deepcopy(row)
    result["terrain"] = "Plains"
    result["assets_loaded"] = True
    result.setdefault("capacity", {})
    result["capacity"].update({"max_workers_allowed": 5, "worker_count": 1, "power_core_staked": True})
    result["workers"] = result.get("workers") or []
    if state == "completion":
        result["swap_targets"] = []
        result["swap_eligibility"] = None
        result["worksite_completion"] = {
            "kind": "worksite_completion",
            "project_id": "mock-project",
            "to": "grain",
            "to_label": "Grain Farm",
            "grain_cost": 123,
        }
    elif state == "refused":
        result["swap_targets"] = [_tile("grain", True), _tile("research")]
        result["swap_eligibility"] = {
            "eligible": False,
            "notice": {
                "code": "worksite_swap_refused_no_pp",
                "kind": "hazard",
                "severity": "danger",
                "blocking": "confirm",
                "message": "This worksite cannot be changed until the plot has production power and a worker.",
            },
        }
        result["worksite_completion"] = None
    elif state == "in_progress":
        result["swap_targets"] = []
        result["swap_eligibility"] = None
        result["worksite_completion"] = None
        result["diagnostics"] = ["construction_incomplete"]
    else:
        result["swap_targets"] = [_tile("grain", True), _tile("research"), _tile("aura"), _tile("wood")]
        result["swap_eligibility"] = {"eligible": True}
        result["worksite_completion"] = None
    return result


def _install_state_routes(page: Page, state: str) -> dict[str, str]:
    selected: dict[str, str] = {}

    def board_handler(route) -> None:
        response = route.fetch()
        payload = response.json()
        plots = payload.get("plots") or []
        assert plots, "the live board fixture must provide a plot to route-mock"
        index = next((i for i, plot in enumerate(plots) if plot.get("deed_uid")), 0)
        row = _apply_state(plots[index], state)
        selected["uid"] = row["deed_uid"]
        payload["plots"][index] = row
        route.fulfill(response=response, body=json.dumps(payload))

    def plot_handler(route) -> None:
        uid = selected.get("uid")
        path = route.request.url.split("?", 1)[0]
        if not uid or not (path.endswith(f"/api/plots/{uid}") or path.endswith(f"/api/plots/{uid}/expand")):
            route.continue_()
            return
        response = route.fetch()
        payload = _apply_state(response.json(), state)
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/board*", board_handler)
    page.route("**/api/plots/**", plot_handler)
    return selected


def _open_modal(live_browser: tuple[Browser, str], state: str) -> tuple[Page, dict[str, str]]:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    selected = _install_state_routes(page, state)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    uid = selected["uid"]
    row = page.locator(f'tr.plot-row[data-deed-uid="{uid}"]')
    row.click()
    expanded = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] .slot-editor')
    expanded.wait_for(timeout=30_000)
    button = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] button[data-slot-action="open-worksite-modal"]')
    button.wait_for(timeout=30_000)
    button.click()
    page.locator("#worksite-modal-overlay").wait_for(state="visible", timeout=10_000)
    return page, selected


def _title_parts(page: Page) -> tuple[str, str]:
    """The modal title is the state's name followed by the plot's region badge."""
    title = page.locator("#worksite-modal-title")
    name = title.evaluate("el => [...el.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent).join('').trim()")
    badge = title.locator(".ch-plot-badge")
    assert badge.count() == 1
    return name, badge.inner_text().strip()


def test_completion_state_title_names_completion(live_browser) -> None:
    """Mutation: restore the unconditional `Change worksite` title; red result: the completion assertion rendered `Change worksite — Plot 065-09-083` instead of the required prefix."""
    page, _ = _open_modal(live_browser, "completion")
    panel = page.locator(".worksite-modal-panel")
    assert panel.get_attribute("data-worksite-state") == "completion"
    name, plot = _title_parts(page)
    assert name == "Complete worksite"
    assert re.fullmatch(r"\d{3}-\d{2}-\d{3}", plot), plot
    page.close()


@pytest.mark.parametrize("state", ["choose", "refused"])
def test_worksite_modal_state_title_and_panel_state(live_browser, state: str) -> None:
    """Mutation: restore the unconditional `Change worksite` title; red result: the refused title rendered the generic prefix."""
    page, _ = _open_modal(live_browser, state)
    panel = page.locator(".worksite-modal-panel")
    assert panel.get_attribute("data-worksite-state") == state
    prefix = {"choose": "Change worksite", "refused": "Cannot change worksite"}[state]
    name, plot = _title_parts(page)
    assert name == prefix
    assert re.fullmatch(r"\d{3}-\d{2}-\d{3}", plot), plot
    page.close()


def test_completion_panel_is_compact(live_browser) -> None:
    """Mutation: remove the 34rem state-width rule; red result: the completion panel measured 1090px wide and the <=700px assertion failed."""
    # The worksite window is a house modal now (`.harvest-panel.worksite-panel`, min 60rem
    # wide like the Fill modal; the old 34rem state-width rules went with the old panel).
    # Compact therefore means: no wider than that house width, no tile grid, and
    # shorter than the same plot's choose state.
    page, _ = _open_modal(live_browser, "completion")
    rect = page.locator(".worksite-modal-panel").evaluate("el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; }")
    assert page.locator(".worksite-modal-body .worksite-tile-option").count() == 0
    page.close()
    page, _ = _open_modal(live_browser, "choose")
    choose = page.locator(".worksite-modal-panel").evaluate("el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; }")
    page.close()
    assert rect["width"] <= 64 * 16, rect  # 60rem house width plus its own content; the old full-width panel measured 1090px
    assert rect["height"] < choose["height"], (rect, choose)


def test_completion_art_is_square_and_local(live_browser) -> None:
    """Mutations: point `worksiteTileArtPath` at `https://d36mxiodymuqjm.cloudfront.net/website/land/productionBuilding` (red: remote-origin assertion failed), or restore the inner `aspect-ratio: 1.286` (red: inner art measured 120x93.3125px)."""
    page, _ = _open_modal(live_browser, "completion")
    art = page.locator(".worksite-completion-art")
    inner = art.locator(".worksite-tile-art")
    image = inner.evaluate("el => getComputedStyle(el).backgroundImage")
    rect = art.evaluate("el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; }")
    inner_rect = inner.evaluate("el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; }")
    assert image.startswith("url(\"http://") or image.startswith("url('http://")
    assert "productionBuildingSquare" in image
    assert abs(rect["width"] - rect["height"]) <= 2
    assert abs(inner_rect["width"] - inner_rect["height"]) <= 2
    page.close()


@pytest.mark.parametrize("state", ["choose", "refused", "completion"])
def test_exactly_one_dismiss_control(live_browser, state: str) -> None:
    """Mutation: re-add the header `×`; red result: the first choose-state run measured 2 close controls instead of 1."""
    page, _ = _open_modal(live_browser, state)
    assert page.locator('[data-worksite-action="close"]').count() == 1
    page.close()


@pytest.mark.parametrize("state", ["choose", "completion"])
def test_primary_action_is_in_the_footer(live_browser, state: str) -> None:
    """Mutation: add `Feed workers` back as a `.slot-review` button in the completion body; red result: the body-button assertion measured 1 instead of 0."""
    page, _ = _open_modal(live_browser, state)
    primary = page.locator(".worksite-modal-confirm")
    assert primary.count() == 1
    assert primary.evaluate("el => !!el.closest('.worksite-modal-panel > footer')")
    assert page.locator(".worksite-modal-body button[class*='slot-review']").count() == 0
    page.close()


def test_build_in_progress_offers_no_dialog(live_browser) -> None:
    """Mutation: make the frontend worksite-control guard ignore `construction_incomplete`; red result: a forbidden `worksite-change-btn` rendered (count 1 instead of 0)."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    selected = _install_state_routes(page, "in_progress")
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    uid = selected["uid"]
    row = page.locator(f'tr.plot-row[data-deed-uid="{uid}"]')
    row.click()
    expanded = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] .slot-editor')
    expanded.wait_for(timeout=30_000)
    button = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] button[data-slot-action="open-worksite-modal"]')
    assert button.count() == 0
    page.evaluate("() => document.querySelector(\"button[data-slot-action='open-worksite-modal']\")?.click()")
    assert page.locator("#worksite-modal-overlay").count() == 0 or page.locator("#worksite-modal-overlay").is_hidden()
    page.close()


def test_refused_state_is_narrow_and_has_no_confirm(live_browser) -> None:
    """Mutation: remove the refused/completion 34rem rule; red result: the refused panel measured 1090px wide and the <=700px assertion failed."""
    page, _ = _open_modal(live_browser, "refused")
    panel = page.locator(".worksite-modal-panel")
    assert panel.get_attribute("data-worksite-state") == "refused"
    # house-modal width (60rem, see test_completion_panel_is_compact), never the old full width
    assert panel.bounding_box()["width"] <= 64 * 16
    assert page.locator(".worksite-modal-confirm").is_hidden()
    assert page.locator(".worksite-modal-tc").is_hidden()
    assert "cannot be changed" in page.locator(".worksite-modal-body").inner_text().lower()
    page.close()


@pytest.mark.parametrize("state", ["choose", "refused", "completion"])
def test_focus_lands_inside_the_panel_in_every_state(live_browser, state: str) -> None:
    """Mutation: delete `focusTarget?.focus()`; red result: the choose-state active element was outside the panel."""
    page, _ = _open_modal(live_browser, state)
    assert page.locator(".worksite-modal-panel").evaluate("el => el.contains(document.activeElement)")
    page.close()


def test_no_remote_asset_host_in_worksite_modal_source() -> None:
    """Mutation: re-add `WORKSITE_CDN_BASE` inside the worksite resolver; red result: the source guard found that constant and failed."""
    source = read_entry_source()
    start = source.index("function worksiteTileArtPath")
    end = source.index("function editorSceneStyle", start)
    modal_asset_source = source[start:end]
    assert "WORKSITE_CDN_BASE" not in modal_asset_source
    assert "cloudfront.net" not in modal_asset_source


def test_every_legal_target_terrain_resolves_to_a_local_file() -> None:
    """Mutation: change the resolver's `research_badlands.webp` path to `research_badlands.missing.webp` (simulating one absent legal file); red result: the existence assertion named that missing path."""
    source = read_entry_source()
    constants = source[source.index("const WORKSITE_FAMILY_B_NATIVE_TERRAIN"):source.index("function worksiteOverlayPath")]
    function = source[source.index("function worksiteTileArtPath"):source.index("function editorSceneStyle")]
    script = (
        "const ASSET_BASE = '/static/assets';\n"
        + constants
        + function
        + "const targets = "
        + json.dumps({target: sorted(terrains) for target, terrains in LEGAL_TARGETS.items()})
        + ";\n"
        + "for (const [target, terrains] of Object.entries(targets)) for (const terrain of terrains) console.log(worksiteTileArtPath(target, terrain));\n"
    )
    import subprocess

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    paths = result.stdout.splitlines()
    assert len(paths) == sum(len(terrains) for terrains in LEGAL_TARGETS.values())
    # The public tree carries no game artwork: an image counts when it is on disk (a maintainer
    # checkout or an earlier download) or on the reviewed, hash-pinned list Ceres downloads it
    # from on first use (ceres/asset_sources.py).
    sources = ASSETS / "sources.json"
    downloadable = {row["path"] for row in (json.loads(sources.read_text(encoding="utf-8")) if sources.is_file() else [])}
    missing = []
    for path in paths:
        assert path.startswith("/static/assets/land/worksites/productionBuildingSquare/")
        relative = path.removeprefix("/static/assets/")
        if not ((ASSETS / relative).is_file() or relative in downloadable):
            missing.append(relative)
    assert not missing, f"worksite tile art is neither shipped nor downloadable: {missing}"
