"""Live Chromium coverage for the worker tile's Combine chip -- now an ABSENCE guard.

History: the compact `.tile-combine-chip` above the tile (2026-08-29/30 maintainer
corrections, `Combine N`, no glyph) was rendered by `combineTileActionHtml`
(static/combine.js) from editor.js. v0.9.130 (, "delete the worker badge row
completely") removed the row, and with it the only caller: the function is still
defined and imported but nothing calls it, so no `.tile-combine-chip` renders anywhere.
The board opens Combine from the address, `/?combine=<card_detail_id>-<edition>-<foil>`
(`open_combine_by_print`, combine_door.py), and from the worker picker's candidate rows.

The two tests therefore keep their scenario (a REAL occupied tile on the real board
snapshot whose printing is made combinable by mocking `GET /api/idle-cards`) and assert
what renders today: no chip on the tile, and the Combine dialog reachable through the
address door for that same printing. If the chip is ever brought back, restore the shape
and click guards (chip outside `.slot-tile`, `Combine N` label, `pointer-events: auto`
on the chip) from this file's history before .

Uses `spawn_live_ceres` (isolated server).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Page

from .combine_door import open_combine_by_print, serve_idle_cards
from .conftest import settle, spawn_live_ceres

SCREEN_DIR = Path(__file__).parents[2] / "work" / "combine-reachability"


def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.loads(response.read().decode())


def _wait_for_board(page: Page) -> None:
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)


def _two_distinct_workers(base_url: str, board: dict) -> tuple[dict, dict, dict]:
    """A worker to make combinable (`row`, `worker`) plus a SECOND, DIFFERENT worker
    from elsewhere on the board (`other`), kept for callers that need two distinct
    staked identities."""
    found: list[tuple[dict, dict]] = []
    for row in board["plots"]:
        if not row.get("assets_loaded"):
            continue
        detail = _get(base_url, f"/api/plots/{urllib.parse.quote(row['deed_uid'], safe='')}")
        for worker in detail.get("workers") or []:
            if worker.get("asset_ref") and isinstance(worker.get("slot"), int) and worker["slot"] >= 1 \
                    and worker.get("card_detail_id") is not None and worker.get("edition") is not None:
                found.append((row, worker))
                if len(found) >= 2:
                    return found[0][0], found[0][1], found[1][1]
    raise AssertionError("fewer than 2 staked workers with card_detail_id/edition were found -- cannot build the collision fixture")


def _install_idle_cards_mock(page: Page, worker: dict) -> None:
    """Forces `worker`'s printing to read as combinable: two idle copies of its own
    (card_detail_id, edition, foil), well under a synthetic bcx_max so neither reads
    maxed (isCardMaxed/rowCanCombine, app.js)."""
    copies = [
        {
            "uid": f"MOCK-IDLE-{i}", "card_detail_id": worker["card_detail_id"], "edition": worker["edition"],
            "foil": worker.get("foil", False), "bcx": 1, "bcx_max": 10, "available": True, "name": worker.get("name", "Mock Card"),
        }
        for i in range(2)
    ]
    # The page reads the idle pool only when the board reports it loaded (combine_door.py).
    serve_idle_cards(page, copies)


def test_tile_combine_chip_is_absent_from_a_combinable_occupant() -> None:
    """With two idle copies of the occupant's printing (combinable), the expanded plot's
    worker tile still renders no `.tile-combine-chip`, neither beside the tile nor inside it."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    with spawn_live_ceres() as (browser, base_url):
        board = _get(base_url, "/api/board")
        row, worker, _other = _two_distinct_workers(base_url, board)

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, worker)
            page.goto(base_url)
            _wait_for_board(page)
            settle(page)  # prefetchIdleCardsCache() has answered

            page_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
            page_row.wait_for(timeout=15_000)
            page_row.click()
            expanded = page.locator(f"tr.expand-row[data-deed-uid='{row['deed_uid']}']")
            expanded.locator(".slot-editor").wait_for(timeout=15_000)
            tile = expanded.locator(f".slot-group[data-group-id='worker'] .slot-tile[data-slot-index='{worker['slot'] - 1}']")
            tile.first.wait_for(timeout=15_000)
            settle(page)

            assert expanded.locator(".tile-combine-chip").count() == 0, "a tile Combine chip rendered (the badge row was deleted in v0.9.130)"
            assert page.locator('[data-slot-action="combine-tile"]').count() == 0

            page.screenshot(path=str(SCREEN_DIR / "tile-chip-absent.png"))
        finally:
            page.close()


def test_tile_combine_chip_click_opens_the_combine_dialog() -> None:
    """The reachability this chip guarded now rides the address door: for the same combinable
    printing, `/?combine=<print>` opens `#combine-dialog-overlay` (not hidden) and the
    board carries no chip to click."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    with spawn_live_ceres() as (browser, base_url):
        board = _get(base_url, "/api/board")
        row, worker, _other = _two_distinct_workers(base_url, board)

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, worker)
            overlay_hidden = "document.getElementById('combine-dialog-overlay')?.hidden ?? true"
            open_combine_by_print(page, base_url, worker)
            settle(page)
            assert page.evaluate(overlay_hidden) is False, "the address door did not open the combine dialog"
            assert page.locator(".tile-combine-chip").count() == 0

            page.screenshot(path=str(SCREEN_DIR / "tile-chip-click-opens-dialog.png"))
        finally:
            page.close()
