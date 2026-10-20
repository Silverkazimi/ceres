from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from ceres.candidates import rank_candidates

from .conftest import spawn_live_ceres
from .vm_context import read_entry_source

APP_JS = Path(__file__).parents[2] / "ceres" / "static" / "app.js"
NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url

DETAILS = {
    1: {"id": 1, "name": "Red Worker", "color": "Red", "rarity": 1},
    2: {"id": 2, "name": "Blue Worker", "color": "Blue", "rarity": 1},
}


def _report(*, current_base: float = 0, candidates: tuple[tuple[str, float, int], ...] = (("red", 5000, 1), ("blue", 2000, 2))) -> dict:
    deed_uid = "base-pp-plot"
    cards = [
        {
            "asset_ref": ref,
            "classification": {"bucket": "unstaked_apparent"},
            "facts": {"uid": ref, "card_detail_id": detail_id, "land_base_pp": base, "level": 1},
        }
        for ref, base, detail_id in candidates
    ]
    workers = []
    if current_base:
        workers.append({
            "name": "Current Worker", "card_detail_id": 1, "land_base_pp": current_base,
            "total_harvest_pp": current_base, "asset_ref": "current", "stake_ref_uid": deed_uid,
            "asset_kind": "worker_card", "slot": 1,
        })
        cards.append({"asset_ref": "current", "facts": {"card_detail_id": 1, "level": 1}})
    return {
        "plots": [{
            "deed_uid": deed_uid,
            "facts": {"plot": "1-1-1", "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
            "staking_facts": {
                "max_workers_allowed": 5, "total_base_pp_cap": 100000,
                "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False,
                "deed_rarity_boost": 0, "totem_boost": 0, "title_boost": 0, "runi_boost": 0,
                "deed_status_token_boost": 0, "red_biome_modifier": 0, "blue_biome_modifier": -0.5,
                "white_biome_modifier": 0, "black_biome_modifier": 0, "green_biome_modifier": 0,
                "gold_biome_modifier": 0,
            },
        }],
        "workers": workers,
        "cards": cards,
        "unassigned_items": {},
    }


# (Batch C, ruling 4 / row 4): migrated in from node-level string
# slices. Ruling 4: NO product regression -- both node failures were stale tests, not a
# guarded product break. `test_worker_tile_caption_uses_base_pp_not_harvest_pp` asserted a
# per-tile PP figure v0.7.132 (maintainer-approved) deliberately moved OFF the tile entirely, to
# the editor's `.editor-base-pp-row` footer (`renderEditorTile`'s own "LOCAL INVARIANT: tiles
# carry no level and no PP figure" comment, `ceres/static/app.js:3806`; footer built by
# `renderEditorBasePpRow`/`editorBasePpColumn`, app.js:4531-4583).
# `test_picker_promotes_base_pp_and_sends_terrain_toggle`'s real failure was a stale stub
# (`candidateCardImageHtml` gained a real caller since the harness's stub list was last
# updated) -- the `candidate-base` markup this test wants is present verbatim in
# `renderCandidateRow`. Both are retargeted to rendered reads against the live harness, which
# sidesteps the stub-drift class of failure structurally (the real app.js runs unmodified).


def _worker_plot_with_occupant(base_url: str) -> str:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for row in board["plots"]:
        if (row.get("capacity", {}) or {}).get("worker_count", 0) >= 1:
            return row["deed_uid"]
    raise AssertionError("no live plot has an existing worker occupant")


def _open_worker_plot(base_url: str) -> str:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for row in board["plots"]:
        if (row.get("capacity", {}) or {}).get("open_slots", 0) > 0:
            return row["deed_uid"]
    raise AssertionError("no live plot has an open worker slot")


def test_worker_tile_shows_no_pp_figure_and_the_footer_reports_base_pp(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid = _worker_plot_with_occupant(base_url)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    tile = page.locator(".slot-editor:visible .slot-group[data-group-id='worker'] .slot-tile:not(.is-empty)").first
    tile.wait_for(timeout=30_000)
    tile_text = tile.inner_text()
    assert "PP" not in tile_text, f"the tile must carry no PP figure at all (moved to the footer) -- got {tile_text!r}"

    footer_cell = page.locator(".slot-editor:visible .editor-base-pp-row .editor-base-pp-cell.is-filled").first
    footer_cell.wait_for(timeout=30_000)
    aria = footer_cell.get_attribute("aria-label") or ""
    assert aria.startswith("Base PP "), f"footer cell must report Base PP, not Harvest PP: {aria!r}"
    figure = footer_cell.locator("b").text_content()
    assert figure and figure.strip(), "footer Base PP cell rendered no figure"
    page.close()


def test_worker_tile_footer_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST: `renderEditorBasePpRow`'s cell-class line is patched to never add
 `is-filled` regardless of `column.figure` (the shape of a regression that silently
 drops the footer figure back to nothing, with no per-tile figure to fall back on
 either -- exactly the dead end v0.7.132's move would create if the footer broke).
 Injection served via `page.route` on `**/static/app.js*`; watched red: no
 `.is-filled` footer cell is left to find."""
    real_source = read_entry_source()
    anchor = "    const classes = ['editor-base-pp-cell', ...(column.figure ? ['is-filled'] : []), ...column.flags];"
    assert real_source.count(anchor) == 1, "renderEditorBasePpRow's cell-class line moved; update this test's injection string"
    broken_source = real_source.replace(
        anchor,
        "    const classes = ['editor-base-pp-cell', ...column.flags];",
    )
    assert broken_source != real_source

    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    injection_seen = False

    def fulfill_injected_app(route) -> None:
        nonlocal injection_seen
        injection_seen = True
        route.fulfill(status=200, content_type="application/javascript", body=broken_source)

    page.route("**/static/app.js*", fulfill_injected_app)
    deed_uid = _worker_plot_with_occupant(base_url)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    assert injection_seen, "injected app.js response was not intercepted"
    footer_cell = page.locator(".slot-editor:visible .editor-base-pp-row .editor-base-pp-cell.is-filled")
    with pytest.raises(Exception):
        footer_cell.first.wait_for(timeout=5_000)
    page.close()


def test_candidate_row_promotes_base_pp_and_terrain_toggle_changes_the_request(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid = _open_worker_plot(base_url)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")
    empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with page.expect_request("**/api/plots/*/candidates*") as first_request_info:
        empty.click()
    first_url = first_request_info.value.url
    assert "include_terrain_penalised" not in first_url, "terrain-penalised candidates must not be requested until the toggle is checked"

    row = page.locator(".candidate-row").first
    row.wait_for(timeout=30_000)
    base_cell = row.locator(".candidate-metric.candidate-base")
    assert base_cell.locator("small").text_content() == "Base PP"
    figure = base_cell.locator("strong").text_content()
    assert figure and figure.strip() and figure.strip() != "—", f"candidate row promoted no Base PP figure: {figure!r}"

    with page.expect_request("**/api/plots/*/candidates*") as toggled_request_info:
        page.locator("input[data-picker-filter='include_terrain_penalised']").check()
    toggled_params = urllib.parse.urlparse(toggled_request_info.value.url).query
    assert urllib.parse.parse_qs(toggled_params).get("include_terrain_penalised") == ["true"], "checking the toggle must resend the request with include_terrain_penalised=true"
    page.close()


def test_terrain_penalty_is_hidden_by_default_but_toggle_reveals_it() -> None:
    body = rank_candidates(_report(), "base-pp-plot", "worker", player="player0005", now=NOW, details_by_id=DETAILS)
    refs = {row["asset_ref"] for row in body["candidates"]}
    assert "blue" not in refs
    assert body["excluded_counts"]["terrain_penalty"] == 1
    shown = rank_candidates(
        _report(), "base-pp-plot", "worker", player="player0005", now=NOW,
        include_terrain_penalised=True, details_by_id=DETAILS,
    )
    assert "blue" in {row["asset_ref"] for row in shown["candidates"]}
    assert shown["excluded_counts"]["terrain_penalty"] == 0


def test_terrain_penalty_stays_hidden_when_best_unpenalized_card_wastes_headroom() -> None:
    """Guard 4: the maintainer's ruling supersedes the deleted headroom-waste heuristic.
 The old test required a penalised card to appear when the best unpenalised card
 overshot Base PP; the authoritative unchecked state now excludes it.
 Injection: restore the `_base_pp_waste(...) > 0` disjunct; the penalised card
 would reappear and this assertion would go red."""
    body = rank_candidates(
        _report(current_base=98000), "base-pp-plot", "worker", player="player0005", now=NOW,
        include_terrain_penalised=False,
        details_by_id=DETAILS,
    )
    assert {row["asset_ref"] for row in body["candidates"]} == {"red"}
    assert body["excluded_counts"]["terrain_penalty"] == 1


def test_terrain_penalty_stays_hidden_when_only_penalized_cards_exist() -> None:
    """Guard 5: an unchecked request never shows a penalized card, even without an
 unpenalized alternative.
 Injection: restore the `best_unpenalized_base is None` disjunct; the blue card
 would reappear and this assertion would go red."""
    body = rank_candidates(
        _report(candidates=(("blue", 2000, 2),)), "base-pp-plot", "worker", player="player0005", now=NOW,
        include_terrain_penalised=False,
        details_by_id=DETAILS,
    )
    assert {row["asset_ref"] for row in body["candidates"]} == set()
    assert body["excluded_counts"]["terrain_penalty"] == 1
